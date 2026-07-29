import { ArrowLeft, Compass } from "lucide-react";
import { Link } from "react-router-dom";

export function NotFoundPage() {
  return (
    <div className="shell-width page-space not-found">
      <Compass size={42} />
      <div className="eyebrow">404</div>
      <h1>This page is outside the tracked universe.</h1>
      <p>The address may be outdated, or the market may no longer be part of the public index.</p>
      <Link className="button button--primary" to="/markets"><ArrowLeft size={16} /> Return to markets</Link>
    </div>
  );
}
