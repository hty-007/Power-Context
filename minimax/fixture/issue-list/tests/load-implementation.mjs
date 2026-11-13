const variant = process.env.SDD_IMPLEMENTATION ?? 'after';
if (!['before', 'after'].includes(variant)) throw new Error('SDD_IMPLEMENTATION must be before or after');

export const { listIssues } = await import(`../${variant}/list.mjs`);
