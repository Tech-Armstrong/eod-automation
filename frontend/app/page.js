"use client";
import dynamic from "next/dynamic";

// The app only runs in the browser (it reads localStorage and talks to server.py), so skip server rendering.
const App = dynamic(() => import("@/components/App"), { ssr: false });

export default function Page() {
  return <App />;
}
