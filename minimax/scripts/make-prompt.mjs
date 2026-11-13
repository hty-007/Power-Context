import { mkdir, readFile, writeFile } from 'node:fs/promises';
import { dirname } from 'node:path';
import { developmentPrompt } from '../src/prompt.mjs';

const [input, output] = process.argv.slice(2);
if (!input || !output) throw new Error('Usage: node scripts/make-prompt.mjs <context.json> <prompt.txt>');
const context = JSON.parse(await readFile(input, 'utf8'));
await mkdir(dirname(output), { recursive: true });
await writeFile(output, developmentPrompt(context), 'utf8');
