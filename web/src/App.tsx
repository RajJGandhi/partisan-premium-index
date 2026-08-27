import { createBrowserRouter, RouterProvider } from "react-router-dom";
import { AppShell } from "./components/AppShell";
import { HomePage } from "./pages/HomePage";
import { MarketDetailPage } from "./pages/MarketDetailPage";
import { MarketsPage } from "./pages/MarketsPage";
import { MethodologyPage } from "./pages/MethodologyPage";
import { NotFoundPage } from "./pages/NotFoundPage";
import { PPIv15Page } from "./pages/PPIv15Page";
import { RaceDetailPage } from "./pages/RaceDetailPage";
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
      { path: "v15", element: <PPIv15Page /> },
      { path: "v15/race/:raceId", element: <RaceDetailPage /> },
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
