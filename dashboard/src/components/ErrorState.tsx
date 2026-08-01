export function ErrorState({ message, onRetry }: { message: string; onRetry(): void }) {
  return (
    <div className="state error" role="alert">
      <p>Could not load operations.</p>
      <p className="detail">{message}</p>
      <button type="button" onClick={onRetry}>Retry</button>
    </div>
  );
}
