// Phone keyboards rarely report real key presses: they insert, autocorrect
// and (Vietnamese Telex, IMEs) rewrite text in place. The stream keeps a
// typing buffer and forwards each change as backspaces plus inserted text.
export function diffTyped(previous: string, next: string): { backspaces: number; text: string } {
  const before = Array.from(previous), after = Array.from(next);
  let common = 0;
  while (common < before.length && common < after.length && before[common] === after[common]) common++;
  return { backspaces: before.length - common, text: after.slice(common).join("") };
}
