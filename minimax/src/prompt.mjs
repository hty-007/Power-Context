export function developmentPrompt(context) {
  if (context.taskId !== 'assignee-filter' || context.progress.status !== 'in-progress'
    || !context.progress.verification.before || context.progress.verification.after) {
    throw new Error('Use the saved in-progress assignee-filter context after baseline checks.');
  }
  const sources = context.sources.map(source => [
    `[${source.id} · ${source.kind}] ${source.path}:${source.startLine}-${source.endLine}`,
    source.symbol ? `符号 ${source.symbol}` : '',
    source.content,
  ].filter(Boolean).join('\n')).join('\n\n');
  const progress = context.progress;
  const results = progress.verification.before.results.map(({ kind, passed, failed, exitCode }) =>
    `${kind} 通过 ${passed}，失败 ${failed}，退出码 ${exitCode}`).join('；');
  return [
    '你正在实现一个规格驱动开发任务。下面给出了 seekdb 查出的完整工程上下文，以及从 PowerContext 恢复的基线状态。',
    `任务 ${context.task.title}。 ${context.task.request}`,
    `当前规格版本 ${context.revision}。已完成 ${progress.completed.join('、')}。待办 ${progress.pending.join('、')}。基线测试 ${results}。`,
    '请输出一份完整的 JavaScript 模块，供直接写入 fixture/issue-list/after/list.mjs。只用一个 javascript 代码块输出源代码。保持相同的导出名称。只修改 listIssues，不改测试或数据。仅用给定上下文，不调用任何工具。',
    '',
    sources,
  ].join('\n');
}

export function readModelCode(answer) {
  const blocks = [...answer.matchAll(/```(?:javascript|js|mjs)?[ \t]*\r?\n([\s\S]*?)```/g)];
  if (blocks.length !== 1) throw new Error('Expected one JavaScript code block in the MiniMax answer.');
  return `${blocks[0][1].trimEnd()}\n`;
}

export function formatRepairPrompt(instructions) {
  return '<|im_start|>system\nYou are a helpful assistant.<|im_end|>\n'
    + `<|im_start|>user\n${instructions}<|im_end|>\n<|im_start|>assistant\n`;
}

export function assembleImplementation(firstAnswer, repairAnswer) {
  const module = readModelCode(firstAnswer);
  const predicate = readModelCode(repairAnswer).trim();
  if (predicate.includes('\n') || !predicate.startsWith('(!assigneeNeedle || ')) {
    throw new Error('Expected the MiniMax repair to replace only the assignee predicate.');
  }
  const previous = '    (!assigneeNeedle || issue.assignee?.toLowerCase() === assigneeNeedle)';
  if (module.split(previous).length !== 2) throw new Error('The initial answer does not contain exactly one matching predicate.');
  return module.replace(previous, `    ${predicate}`);
}
