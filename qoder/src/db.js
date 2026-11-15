import fs from 'node:fs';
import net from 'node:net';
import { seekdb } from '@oceanbase/seekdb-node';
import mysql from 'mysql2/promise';

// connectionOptions() 是端点信息，不是 mysql2 配置对象：按 transport 判别式适配。
// 实机（Windows + seekdb 1.4.0-community）探针拿到的是 transport=named_pipe，因此命名管道是本平台主路径而非兜底。
// 每条连接必须自己打开一条管道：mysql2 的 stream 传函数时按连接调用（base/connection.js: stream = opts.config.stream(opts)），
// 传已建好的 socket 会让连接池共用同一条管道，协议直接串流。
function toMysqlLocation(ep) {
  if (ep.transport === 'tcp') return { host: ep.host, port: ep.port };
  if (ep.transport === 'unix_socket') return { socketPath: ep.unixSocket };
  if (ep.transport === 'named_pipe') return { stream: () => net.connect(ep.namedPipe) };
  throw new Error(`未知的 seekdb 传输方式：${ep.transport}`);
}

/**
 * 启动/复用本地 seekdb，并建一个 mysql2 连接池（并发请求各用各的连接）。
 * 任一步失败都会关闭已启动的 native instance，避免留下孤立服务进程。
 */
export async function open({ dataDir, memoryBudget = '2G', database = 'test', poolSize = 4 }) {
  fs.mkdirSync(dataDir, { recursive: true });
  const native = await seekdb.open(dataDir, {
    parameters: { memory_budget: memoryBudget, mysql_port_mode: 'random' },
  });
  try {
    const endpoint = native.connectionOptions();
    const pool = mysql.createPool({
      ...toMysqlLocation(endpoint),
      user: endpoint.user,
      password: '',
      database,
      connectionLimit: poolSize,
      waitForConnections: true,
      multipleStatements: false,
    });
    const transport =
      endpoint.transport === 'tcp' ? `tcp ${endpoint.host}:${endpoint.port}`
      : endpoint.transport === 'unix_socket' ? `unix_socket ${endpoint.unixSocket}`
      : `named_pipe ${endpoint.namedPipe}`;
    await pool.query('SELECT 1'); // 立刻验证连接可用
    return {
      native,
      pool,
      endpoint,
      transport,
      async close() {
        await pool.end().catch(() => {});
        native.close();
      },
    };
  } catch (e) {
    native.close();
    throw e;
  }
}

// 实机确认：utf8mb4 表 + WITH PARSER ik 的 FULLTEXT 索引，配合自然语言模式 MATCH 命中中英文
const CHARSET = 'DEFAULT CHARSET=utf8mb4';
const DDL = [
  `CREATE TABLE IF NOT EXISTS exp_record (
     id BIGINT PRIMARY KEY AUTO_INCREMENT,
     fingerprint CHAR(64) NOT NULL,
     name VARCHAR(255) NOT NULL,
     project VARCHAR(127) NOT NULL,
     dataset VARCHAR(127) NOT NULL,
     split VARCHAR(63) NOT NULL,
     task VARCHAR(127),
     model VARCHAR(127),
     seed VARCHAR(63),
     source VARCHAR(255),
     body MEDIUMTEXT NOT NULL,
     line_count INT NOT NULL,
     imported_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
     UNIQUE KEY uk_record_fingerprint (fingerprint)
   ) ${CHARSET}`,
  `CREATE TABLE IF NOT EXISTS exp_metric (
     id BIGINT PRIMARY KEY AUTO_INCREMENT,
     record_id BIGINT NOT NULL,
     name VARCHAR(127) NOT NULL,
     mkey VARCHAR(127) NOT NULL,
     value DOUBLE NOT NULL,
     unit VARCHAR(24) NOT NULL DEFAULT '',
     raw VARCHAR(255),
     line_no INT,
     KEY idx_metric_record (record_id),
     KEY idx_metric_key (mkey)
   ) ${CHARSET}`,
  `CREATE TABLE IF NOT EXISTS bench_meta (
     k VARCHAR(64) PRIMARY KEY,
     v VARCHAR(255) NOT NULL
   ) ${CHARSET}`,
];

export const FULLTEXT_PARSER = 'ik';
export const FT_SQL = (parser = FULLTEXT_PARSER) =>
  `CREATE FULLTEXT INDEX ft_exp_body ON exp_record(body)${parser ? ` WITH PARSER ${parser}` : ''}`;

export async function getMeta(client, k) {
  const [rows] = await client.execute(`SELECT v FROM bench_meta WHERE k = ?`, [k]);
  return rows.length ? rows[0].v : null;
}

export async function setMeta(client, k, v) {
  await client.execute(`REPLACE INTO bench_meta (k, v) VALUES (?, ?)`, [k, String(v)]);
}

/** 建表 + FULLTEXT 索引；ik 不可用时退回默认解析器并把原因一并返回，供界面与取证核对。 */
export async function initSchema(client) {
  for (const sql of DDL) await client.query(sql);
  const indexed = async () => {
    const [rows] = await client.execute(
      `SELECT COUNT(*) AS c FROM information_schema.statistics WHERE table_schema = DATABASE() AND table_name = 'exp_record' AND index_name = 'ft_exp_body'`
    );
    return Number(rows[0].c) > 0;
  };
  let ftParser = FULLTEXT_PARSER;
  let ftError = null;
  if (await indexed()) {
    ftParser = (await getMeta(client, 'ft_parser')) || '';
    if (ftParser === 'default') ftParser = '';
    return { ftParser, ftError: null, indexDdl: FT_SQL(ftParser) };
  }
  try {
    await client.query(FT_SQL(FULLTEXT_PARSER));
  } catch (e) {
    ftError = `ik: ${e.message}`;
    try {
      await client.query(FT_SQL(''));
      ftParser = '';
    } catch (e2) {
      // 元数据滞后时索引可能已建好：确认存在就沿用，否则如实抛出
      if (!(await indexed())) throw e2;
      ftParser = ((await getMeta(client, 'ft_parser')) || '').replace(/^default$/, '');
    }
  }
  await setMeta(client, 'ft_parser', ftParser || 'default');
  return { ftParser, ftError, indexDdl: FT_SQL(ftParser) };
}
