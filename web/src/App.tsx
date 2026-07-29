import { createBrowserRouter, RouterProvider } from "react-router-dom";
import { AppShell } from "./components/AppShell";
import { HomePage } from "./pages/HomePage";
import { MarketDetailPage } from "./pages/MarketDetailPage";
import { MarketsPage } from "./pages/MarketsPage";
import { MethodologyPage } from "./pages/MethodologyPage";
import { NotFoundPage } from "./pages/NotFoundPage";
import { SystemStatusPage } from "./pages/SystemStatusPage";
import { TrackRecordPage } from "./pages/TrackRecordPage";

const router = createBrowserRouter([
  {
    path: "/",
    element: <AppShell />,
    children: [
      { index: true, element: <HomePage /> },
      { path: "markets", element: <MarketsPage /> },
      { path: "markets/:slug", element: <MarketDetailPage /> },
      { path: "track-record", element: <TrackRecordPage /> },
      { path: "methodology", element: <MethodologyPage /> },
      { path: "system-status", element: <SystemStatusPage /> },
      { path: "*", element: <NotFoundPage /> },
    ],
  },
]);

export default function App() {
  return <RouterProvider router={router} />;
}
