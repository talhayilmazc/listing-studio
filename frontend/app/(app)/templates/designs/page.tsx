import { redirect } from "next/navigation";
import { movedTo } from "@/lib/oldAddresses";

/** Moved to Mockups → Make mockups (lib/oldAddresses.ts). */
export default async function OldMakeMockups({ searchParams }: { searchParams: Promise<Record<string, string | string[] | undefined>> }) {
  redirect(movedTo("/templates/designs", await searchParams));
}
