import { redirect } from "next/navigation";
import { movedTo } from "@/lib/oldAddresses";

/** Moved to Mockups → Templates (lib/oldAddresses.ts). */
export default async function OldTemplates({ searchParams }: { searchParams: Promise<Record<string, string | string[] | undefined>> }) {
  redirect(movedTo("/templates", await searchParams));
}
