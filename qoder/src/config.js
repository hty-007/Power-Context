import path from 'node:path';
import os from 'node:os';

const ROOT = path.dirname(import.meta.dirname);

// 数据库位置可配置：--data <dir>（优先）或环境变量 EXPDB_HOME，默认写到项目内 .data/seekdb（已 gitignore）。
export function resolveDataDir(argv = process.argv) {
  const i = argv.indexOf('--data');
  if (i !== -1 && argv[i + 1]) return path.resolve(argv[i + 1]);
  if (process.env.EXPDB_HOME) return path.resolve(process.env.EXPDB_HOME);
  return path.join(ROOT, '.data', 'seekdb');
}

export const cfg = {
  root: ROOT,
  dataDir: resolveDataDir(),
  host: process.env.EXPDB_HOST || '127.0.0.1',
  port: Number(process.env.EXPDB_PORT || 4321),
  memoryBudget: process.env.SEEKDB_MEMORY_BUDGET || '2G',
  poolSize: Number(process.env.EXPDB_POOL_SIZE || 4),
  database: 'test',
  samplesDir: path.join(ROOT, 'samples'),
  publicDir: path.join(ROOT, 'public'),
  tmpTestRoot: os.tmpdir(),
};
