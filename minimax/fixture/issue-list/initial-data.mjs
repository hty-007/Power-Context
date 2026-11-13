const records = [
  { id: 'T-08', title: 'Cache warm-up', status: 'open', assignee: ' Lin ', labels: ['infra', 'infra'] },
  { id: 'T-03', title: 'Cache refresh', status: 'done', assignee: 'LIN', labels: ['infra'] },
  { id: 'T-11', title: 'Cache timeout', status: 'open', assignee: 'Lina', labels: ['infra', 'urgent'] },
  { id: 'T-01', title: 'Cache metrics', status: 'open', assignee: null, labels: ['observability'] },
  { id: 'T-07', title: 'Review errors', status: 'open', assignee: '\u3000lin\t' },
  { id: 'T-14', title: 'Improve index', status: 'open' },
];

export const issues = Object.freeze(records.map(issue => Object.freeze(issue)));
