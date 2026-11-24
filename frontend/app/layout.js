import "@fontsource-variable/newsreader";
import "@fontsource/ibm-plex-sans/400.css";
import "@fontsource/ibm-plex-sans/500.css";
import "@fontsource/ibm-plex-sans/600.css";
import "./globals.css";

const FAVICON =
  "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'%3E%3Crect width='32' height='32' rx='7' fill='%234f6b5a'/%3E%3Cpath d='M9 11h14M9 16h14M9 21h9' stroke='%23f5f2ec' stroke-width='2.2' stroke-linecap='round'/%3E%3C/svg%3E";

export const metadata = {
  title: "EOD - End of Day",
  description: "Summaries of your inbox, with your own notes alongside.",
  icons: { icon: FAVICON },
};

export const viewport = { width: "device-width", initialScale: 1 };

export default function RootLayout({ children }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
