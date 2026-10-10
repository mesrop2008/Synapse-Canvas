import { useCallback, useEffect, useState } from 'react';

/** Seconds left, and a function that restarts the count. Read off the clock
 *  rather than counted in ticks, which background tabs throttle. */
export function useCountdown(initialSeconds = 0): [number, (seconds: number) => void] {
  const [deadline, setDeadline] = useState(() => Date.now() + initialSeconds * 1000);
  const [now, setNow] = useState(() => Date.now());
  const secondsLeft = Math.max(0, Math.ceil((deadline - now) / 1000));
  const running = secondsLeft > 0;

  useEffect(() => {
    if (!running) return;
    const timer = window.setInterval(() => setNow(Date.now()), 250);
    return () => window.clearInterval(timer);
  }, [running]);

  const restart = useCallback((seconds: number) => {
    const startedAt = Date.now();
    setNow(startedAt);
    setDeadline(startedAt + seconds * 1000);
  }, []);

  return [secondsLeft, restart];
}

/** 75 -> "1:15". */
export function formatCountdown(seconds: number): string {
  const minutes = Math.floor(seconds / 60);
  return `${minutes}:${String(seconds % 60).padStart(2, '0')}`;
}
