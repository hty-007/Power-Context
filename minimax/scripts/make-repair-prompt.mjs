import { mkdir, readFile, writeFile } from 'node:fs/promises';
import { dirname } from 'node:path';
import { formatRepairPrompt } from '../src/prompt.mjs';

const [input, output] = process.argv.slice(2);
if (!input || !output) throw new Error('Usage: node scripts/make-repair-prompt.mjs <instructions.txt> <chatml.txt>');
const instructions = (await readFile(input, 'utf8')).replace(/\r\n/g, '\n');
await mkdir(dirname(output), { recursive: true });
await writeFile(output, formatRepairPrompt(instructions), 'utf8');
