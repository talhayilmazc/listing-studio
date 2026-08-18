export default function PrivacyPage() {
  return (
    <article className="prose mx-auto max-w-3xl space-y-4">
      <h1 className="text-2xl font-semibold text-slate-900">Privacy Policy</h1>
      <p className="text-sm text-slate-500">Placeholder — final policy to be published before launch.</p>
      <p className="text-slate-700">
        We store only what is needed to provide the service: your uploaded designs, generated draft
        content, and OAuth tokens for shops you connect. Tokens are encrypted at rest and are never
        shown in the interface.
      </p>
      <p className="text-slate-700">
        Etsy-sourced content is cached only for as long as needed to provide the service and is
        removed on the schedule described in our data-retention policy. Disconnecting a shop deletes
        the Etsy-sourced content associated with it.
      </p>
    </article>
  );
}
