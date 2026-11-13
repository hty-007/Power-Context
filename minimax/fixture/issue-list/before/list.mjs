export function listIssues(issues, { status, query } = {}) {
  const needle = (query ?? '').trim().toLowerCase();

  return issues.filter(issue =>
    (!status || issue.status === status) &&
    (!needle || issue.title.toLowerCase().includes(needle))
  );
}
