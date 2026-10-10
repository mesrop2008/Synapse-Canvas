import { forwardRef } from 'react';

interface CodeInputProps {
  id: string;
  value: string;
  onChange: (code: string) => void;
  placeholder: string;
  autoFocus?: boolean;
  disabled?: boolean;
}

/** A six-digit emailed code. Pasting "123 456" or "123-456" still works. */
export const CodeInput = forwardRef<HTMLInputElement, CodeInputProps>(
  function CodeInput({ id, value, onChange, placeholder, autoFocus, disabled }, ref) {
    return (
      <input
        ref={ref}
        id={id}
        className="input input-code"
        type="text"
        inputMode="numeric"
        // Lets iOS and Android offer the code straight from the email.
        autoComplete="one-time-code"
        // No maxLength: it would cut a pasted "123 456" to "123 45"
        // before the filter below could drop the space.
        placeholder={placeholder}
        autoFocus={autoFocus}
        disabled={disabled}
        value={value}
        onChange={(event) => onChange(event.target.value.replace(/\D/g, '').slice(0, 6))}
      />
    );
  },
);
