/**
 * Identical requests asked for at the same moment (the sidebar, the top strip and the page
 * each asking for the profiles) share one: ``shared(key, run)`` starts ``run`` only if no
 * request with that key is on its way, and every caller waits for the same answer. Nothing
 * is kept once it has answered: the next ask starts a new request.
 */
export function shareInFlight<T>(): (key: string, run: () => Promise<T>) => Promise<T> {
  const going = new Map<string, Promise<T>>();
  return (key, run) => {
    const now = going.get(key);
    if (now) return now;
    const started = run();
    going.set(key, started);
    const clear = () => going.delete(key);
    started.then(clear, clear);
    return started;
  };
}
