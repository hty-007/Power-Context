export function countLabels(issues) {
  const counts = new Map();
  for (const issue of issues) {
    for (const label of new Set(issue.labels ?? [])) {
      counts.set(label, (counts.get(label) ?? 0) + 1);
    }
  }
  return counts;
}
