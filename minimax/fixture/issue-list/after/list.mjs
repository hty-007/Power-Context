export function listIssues(issues, { status, query, assignee } = {}) {
  const needle = (query ?? '').trim().toLowerCase();
  const assigneeNeedle = (assignee ?? '').trim().toLowerCase();

  return issues.filter(issue =>
    (!status || issue.status === status) &&
    (!needle || issue.title.toLowerCase().includes(needle)) &&
    (!assigneeNeedle || (issue.assignee ?? '').trim().toLowerCase() === assigneeNeedle)
  );
}
