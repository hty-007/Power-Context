Here is the corrected single predicate line for the `listIssues` function:

```javascript
(!assigneeNeedle || (issue.assignee ?? '').trim().toLowerCase() === assigneeNeedle)
```

This line ensures that if `issue.assignee` is null or undefined, it is treated as an empty string before trimming and lowercasing, thus correctly handling cases where `issue.assignee` might be nullish.