export function allLoadsSucceeded(results) {
  return results.length > 0 && results.every(result => result?.status === 'fulfilled' && result.value === true);
}
