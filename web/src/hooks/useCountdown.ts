import { useCallback, useEffect, useState } from 'react';

/** Seconds left, a function that restarts the count, and the length of the
 *  current count. Read off the clock rather than counted in ticks, which
 *  background tabs throttle. */
export function useCountdown(
  initialSeconds = 0,
): [number, (seconds: number) => void, number] {
  const [deadline, setDeadline] = useState(() => Date.now() + initialSeconds * 1000);
  const [duration, setDuration] = useState(initialSeconds);
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
    setDuration(seconds);
    setDeadline(startedAt + seconds * 1000);
  }, []);

  return [secondsLeft, restart, duration];
}

/** 75 -> "1:15". */
export function formatCountdown(seconds: number): string {
  const minutes = Math.floor(seconds / 60);
  return `${minutes}:${String(seconds % 60).padStart(2, '0')}`;
}
