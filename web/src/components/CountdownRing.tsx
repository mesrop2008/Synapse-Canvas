import { formatCountdown } from '../hooks/useCountdown';

interface CountdownRingProps {
  secondsLeft: number;
  /** What a full ring stands for. */
  totalSeconds: number;
  label: string;
}

const RADIUS = 20;
const CIRCUMFERENCE = 2 * Math.PI * RADIUS;

/** Empties as the time runs out, with the time left in the middle. */
export function CountdownRing({ secondsLeft, totalSeconds, label }: CountdownRingProps) {
  const remaining = totalSeconds > 0 ? Math.min(secondsLeft / totalSeconds, 1) : 0;

  return (
    // role="timer" is not a live region, so a reader is not told every second.
    <div className="countdown" role="timer" aria-label={label}>
      <svg viewBox="0 0 48 48" aria-hidden="true">
        <circle className="countdown-track" cx="24" cy="24" r={RADIUS} />
        <circle
          className="countdown-arc"
          cx="24"
          cy="24"
          r={RADIUS}
          strokeDasharray={CIRCUMFERENCE}
          strokeDashoffset={CIRCUMFERENCE * (1 - remaining)}
        />
      </svg>
      <span className="countdown-time" aria-hidden="true">
        {formatCountdown(secondsLeft)}
      </span>
    </div>
  );
}
