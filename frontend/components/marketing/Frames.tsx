import Image from "next/image";

/**
 * Device frames drawn in CSS around real screenshots of the app (taken from a
 * demo account holding sample data, scripts in docs/marketing-screens.md).
 * The frame is markup, so it is sharp at any size; the screenshot is served
 * at the width the layout needs, from a 2x original.
 */

export function BrowserFrame({
  src,
  alt,
  width,
  height,
  sizes,
  priority = false,
  url = "listyro.com",
}: {
  src: string;
  alt: string;
  width: number;
  height: number;
  sizes: string;
  priority?: boolean;
  url?: string;
}) {
  return (
    <figure className="overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-[0_1px_2px_rgba(0,0,0,.04),0_30px_70px_-34px_rgba(28,25,23,.32)]">
      <div className="flex items-center gap-3 border-b border-slate-200 bg-stone-50 px-4 py-2.5" aria-hidden>
        <span className="flex gap-1.5">
          <i className="h-2.5 w-2.5 rounded-full bg-slate-200" />
          <i className="h-2.5 w-2.5 rounded-full bg-slate-200" />
          <i className="h-2.5 w-2.5 rounded-full bg-slate-200" />
        </span>
        <span className="mx-auto w-full max-w-xs truncate rounded-md border border-slate-200 bg-white px-3 py-0.5 text-center text-[11px] text-slate-500">
          {url}
        </span>
        <span className="w-10" />
      </div>
      <Image src={src} alt={alt} width={width} height={height} sizes={sizes} priority={priority} className="block h-auto w-full" />
    </figure>
  );
}

export function PhoneFrame({
  src,
  alt,
  width,
  height,
  sizes,
}: {
  src: string;
  alt: string;
  width: number;
  height: number;
  sizes: string;
}) {
  return (
    <figure className="mx-auto w-full max-w-[19rem] rounded-[2.6rem] bg-slate-900 p-2.5 shadow-[0_30px_70px_-30px_rgba(28,25,23,.5)]">
      <div className="relative overflow-hidden rounded-[2.05rem] bg-white">
        <span aria-hidden className="absolute left-1/2 top-2 z-10 h-5 w-20 -translate-x-1/2 rounded-full bg-slate-900" />
        <Image src={src} alt={alt} width={width} height={height} sizes={sizes} className="block h-auto w-full" />
      </div>
    </figure>
  );
}
