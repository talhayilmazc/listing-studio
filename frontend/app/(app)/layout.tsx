import { AppShell } from "@/components/AppShell";
import { MockupRunWatcher } from "@/components/MockupRunWatcher";
import { SessionProvider } from "@/components/SessionProvider";

/** Everything behind sign-in, plus the sign-in screens themselves. */
export default function AppLayout({ children }: { children: React.ReactNode }) {
  return (
    <SessionProvider>
      <AppShell>{children}</AppShell>
      {/* "Mockups ready" from any page (seller feedback D4). */}
      <MockupRunWatcher />
    </SessionProvider>
  );
}
