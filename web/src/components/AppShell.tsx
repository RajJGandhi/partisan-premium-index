import { BarChart3, BookOpenText, Menu, Moon, Orbit, Sun, X } from "lucide-react";
import { useEffect, useState } from "react";
import { Link, NavLink, Outlet, useLocation } from "react-router-dom";

const navItems = [
  ["Markets", "/markets"],
  ["Track record", "/track-record"],
  ["Methodology", "/methodology"],
  ["System status", "/system-status"],
] as const;

export function AppShell() {
  const location = useLocation();
  const [menuOpen, setMenuOpen] = useState(false);
  const [dark, setDark] = useState(() => {
    const stored = localStorage.getItem("ppi-theme");
    return stored ? stored === "dark" : window.matchMedia("(prefers-color-scheme: dark)").matches;
  });

  useEffect(() => {
    document.documentElement.dataset.theme = dark ? "dark" : "light";
    localStorage.setItem("ppi-theme", dark ? "dark" : "light");
  }, [dark]);

  useEffect(() => {
    setMenuOpen(false);
    window.scrollTo({ top: 0, behavior: "smooth" });
  }, [location.pathname]);

  return (
    <div className="site-shell">
      <a className="skip-link" href="#main-content">Skip to content</a>
      <header className="site-header">
        <div className="shell-width site-header__inner">
          <Link className="brand" to="/" aria-label="Partisan Premium Index home">
            <span className="brand__mark"><Orbit size={21} aria-hidden="true" /></span>
            <span>
              <strong>Partisan Premium Index</strong>
              <small>Independent political fair values</small>
            </span>
          </Link>

          <nav className={`site-nav${menuOpen ? " site-nav--open" : ""}`} aria-label="Main navigation">
            {navItems.map(([label, href]) => (
              <NavLink key={href} to={href} className={({ isActive }) => (isActive ? "active" : "")}>{label}</NavLink>
            ))}
          </nav>

          <div className="site-header__actions">
            <button className="icon-button" type="button" onClick={() => setDark((value) => !value)} aria-label={`Switch to ${dark ? "light" : "dark"} theme`}>
              {dark ? <Sun size={18} /> : <Moon size={18} />}
            </button>
            <button className="icon-button mobile-menu-button" type="button" onClick={() => setMenuOpen((value) => !value)} aria-label="Toggle navigation" aria-expanded={menuOpen}>
              {menuOpen ? <X size={20} /> : <Menu size={20} />}
            </button>
          </div>
        </div>
      </header>

      <main id="main-content">
        <Outlet />
      </main>

      <footer className="site-footer">
        <div className="shell-width site-footer__grid">
          <div>
            <div className="brand brand--footer">
              <span className="brand__mark"><Orbit size={20} aria-hidden="true" /></span>
              <strong>Partisan Premium Index</strong>
            </div>
            <p>A transparent public record of political prediction-market prices, independent fair values, and revisions.</p>
          </div>
          <div>
            <strong><BarChart3 size={16} /> Research standard</strong>
            <p>Published history is preserved. Fair values require human approval. The public site contains no database credentials or admin interface.</p>
          </div>
          <div>
            <strong><BookOpenText size={16} /> Important</strong>
            <p>Independent research only. Not investment advice, a trading signal, or a claim of political neutrality by market participants.</p>
          </div>
        </div>
        <div className="shell-width site-footer__bottom">
          <span>© 2026 Partisan Premium Index</span>
          <Link to="/methodology">Read the methodology</Link>
        </div>
      </footer>
    </div>
  );
}
