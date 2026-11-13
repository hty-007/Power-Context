import { seekdb } from '@oceanbase/seekdb-node';
import mysql from 'mysql2/promise';
import { join } from 'node:path';
import { project } from './fixture.mjs';

const contextSQL = `SELECT c.revision, s.source_id AS id, s.kind, s.source_path AS path, s.symbol,
       s.start_line AS startLine, s.end_line AS endLine, s.content, s.content_hash AS sha256
FROM sdd_task_sources m
JOIN sdd_sources s ON s.project_id = m.project_id AND s.source_id = m.source_id
JOIN sdd_corpus c ON c.project_id = s.project_id
WHERE m.project_id = ? AND m.task_id = ?
ORDER BY FIELD(s.kind, 'spec', 'code', 'test', 'data'), s.source_id`;

async function schema(connection) {
  await connection.query(`CREATE TABLE IF NOT EXISTS sdd_corpus (
    project_id VARCHAR(40) PRIMARY KEY, revision VARCHAR(20) NOT NULL
  )`);
  await connection.query(`CREATE TABLE IF NOT EXISTS sdd_sources (
    project_id VARCHAR(40) NOT NULL, source_id VARCHAR(40) NOT NULL, kind VARCHAR(10) NOT NULL,
    source_path VARCHAR(240) NOT NULL, symbol VARCHAR(80),
    start_line INT NOT NULL, end_line INT NOT NULL, content TEXT NOT NULL, content_hash CHAR(64) NOT NULL,
    PRIMARY KEY (project_id, source_id)
  )`);
  await connection.query(`CREATE TABLE IF NOT EXISTS sdd_task_sources (
    project_id VARCHAR(40) NOT NULL, task_id VARCHAR(40) NOT NULL, source_id VARCHAR(40) NOT NULL,
    PRIMARY KEY (project_id, task_id, source_id)
  )`);
}

export async function useSeekdb(runtime, action) {
  const engine = await seekdb.open(join(runtime, 'seekdb'), {
    parameters: { memory_budget: '2G', mysql_port_mode: 'random' },
  });
  let connection;
  try {
    const endpoint = engine.connectionOptions();
    const address = endpoint.transport === 'tcp'
      ? { host: endpoint.host, port: endpoint.port }
      : { socketPath: endpoint.unixSocket ?? endpoint.namedPipe };
    connection = await mysql.createConnection({
      ...address, user: endpoint.user, password: '', database: 'test', charset: 'utf8mb4',
    });
    await schema(connection);
    const [[{ version }]] = await connection.query('SELECT VERSION() AS version');
    return await action(connection, version);
  } finally {
    try { await connection?.end(); } finally { engine.close(); }
  }
}

export async function importSources(connection, revision, sources) {
  await connection.beginTransaction();
  try {
    await connection.execute('DELETE FROM sdd_task_sources WHERE project_id = ?', [project]);
    await connection.execute('DELETE FROM sdd_sources WHERE project_id = ?', [project]);
    for (const s of sources) {
      await connection.execute(`INSERT INTO sdd_sources
        (project_id, source_id, kind, source_path, symbol, start_line, end_line, content, content_hash)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)`,
      [project, s.id, s.kind, s.path, s.symbol, s.startLine, s.endLine, s.content, s.sha256]);
      for (const task of s.tasks) {
        await connection.execute('INSERT INTO sdd_task_sources (project_id, task_id, source_id) VALUES (?, ?, ?)',
          [project, task, s.id]);
      }
    }
    await connection.execute(`INSERT INTO sdd_corpus (project_id, revision) VALUES (?, ?)
      ON DUPLICATE KEY UPDATE revision = VALUES(revision)`, [project, revision]);
    await connection.commit();
  } catch (error) {
    await connection.rollback();
    throw error;
  }
  const [[row]] = await connection.execute('SELECT COUNT(*) AS source_count FROM sdd_sources WHERE project_id = ?', [project]);
  return Number(row.source_count);
}

export async function retrieveSources(connection, taskId) {
  const parameters = [project, taskId];
  const [rows] = await connection.execute(contextSQL, parameters);
  if (!rows.length) throw new Error('No sources for this task. Import the fixture first.');
  const sources = rows.map(({ revision, ...source }) => source);
  return { revision: rows[0].revision, sources, query: { sql: contextSQL, parameters, rows: rows.length } };
}
