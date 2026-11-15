import { parseRecord } from './parse.js';

/** 在连接池上借一条连接开事务；传入普通连接时直接复用。失败必定回滚，不留半条记录。 */
export async function withTransaction(client, fn) {
  const pooled = typeof client.getConnection === 'function';
  const conn = pooled ? await client.getConnection() : client;
  try {
    await conn.beginTransaction();
    const out = await fn(conn);
    await conn.commit();
    return out;
  } catch (e) {
    await conn.rollback().catch(() => {});
    throw e;
  } finally {
    if (pooled) conn.release();
  }
}

/**
 * 导入一份 Markdown 实验记录：记录与指标在同一事务内写入，失败整体回滚。
 * 内容指纹命中已有记录时返回已有记录；并发同内容导入靠唯一索引 + 一次重试收敛到同一条。
 */
async function doImport(client, rec, source) {
  return withTransaction(client, async (conn) => {
    const [dup] = await conn.execute(`SELECT id, name, imported_at FROM exp_record WHERE fingerprint = ?`, [rec.fingerprint]);
    if (dup.length) return { duplicate: true, id: Number(dup[0].id), name: dup[0].name, importedAt: dup[0].imported_at, parsed: rec };
    const [res] = await conn.execute(
      `INSERT INTO exp_record (fingerprint, name, project, dataset, split, task, model, seed, source, body, line_count)
       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
      [rec.fingerprint, rec.name, rec.project, rec.dataset, rec.split, rec.task, rec.model, rec.seed, source, rec.body, rec.lineCount]
    );
    const id = Number(res.insertId);
    for (const m of rec.metrics) {
      await conn.execute(
        `INSERT INTO exp_metric (record_id, name, mkey, value, unit, raw, line_no) VALUES (?, ?, ?, ?, ?, ?, ?)`,
        [id, m.name, m.key, m.value, m.unit, m.raw || '', m.line || 0]
      );
    }
    return { duplicate: false, id, parsed: rec };
  });
}

export async function importRecord(client, markdown, source = '') {
  const fallback = source ? source.replace(/^.*[\\/]/, '').replace(/\.md$/i, '') : '未命名实验';
  const rec = parseRecord(markdown, fallback);
  for (let attempt = 0; ; attempt++) {
    try {
      return await doImport(client, rec, source);
    } catch (e) {
      // 唯一索引撞车说明另一个请求先落库了：换新事务重跑一次，第二次必然走指纹命中分支
      if (e && e.code === 'ER_DUP_ENTRY' && attempt === 0) continue;
      throw e;
    }
  }
}

export async function listRecords(client) {
  const [rows] = await client.execute(
    `SELECT r.id, r.name, r.project, r.dataset, r.split, r.source, r.imported_at, r.line_count,
            (SELECT COUNT(*) FROM exp_metric m WHERE m.record_id = r.id) AS metric_count
     FROM exp_record r ORDER BY r.id`
  );
  return rows;
}

export async function getRecord(client, id) {
  const [rows] = await client.execute(`SELECT * FROM exp_record WHERE id = ?`, [id]);
  if (!rows.length) return null;
  const [metrics] = await client.execute(
    `SELECT name, mkey, value, unit, raw, line_no FROM exp_metric WHERE record_id = ? ORDER BY id`,
    [id]
  );
  return { ...rows[0], metrics };
}

export async function filters(client) {
  const [projects] = await client.execute(`SELECT project, COUNT(*) AS c FROM exp_record GROUP BY project ORDER BY project`);
  const [datasets] = await client.execute(`SELECT dataset, COUNT(*) AS c FROM exp_record GROUP BY dataset ORDER BY dataset`);
  return {
    projects: projects.map((r) => ({ value: r.project, count: Number(r.c) })),
    datasets: datasets.map((r) => ({ value: r.dataset, count: Number(r.c) })),
  };
}

export async function stats(client) {
  const [[r]] = await client.execute(`SELECT COUNT(*) AS records FROM exp_record`);
  const [[m]] = await client.execute(`SELECT COUNT(*) AS metrics FROM exp_metric`);
  return { records: Number(r.records), metrics: Number(m.metrics), fingerprintAlgorithm: 'sha256(normalize(body))' };
}
