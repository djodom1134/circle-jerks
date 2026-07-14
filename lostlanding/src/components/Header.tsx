export function Header() {
  return (
    <header className="topbar">
      <a className="brand" href="#top">
        <span className="wing" aria-hidden="true">
          ◆
        </span>
        THE LOST LANDING
        <span className="wing" aria-hidden="true">
          ◆
        </span>
      </a>
      <nav aria-label="Section navigation">
        <a href="#calculator">Calculator</a>
        <a href="#daily">Daily Count</a>
        <a href="#ledger">Operator Ledger</a>
        <a href="#based-here">Who's Based Here</a>
        <a href="#method">Method</a>
        <a href="#obligation">The Obligation</a>
        <a href="#lawful-use">What the Law Allows</a>
      </nav>
    </header>
  );
}
