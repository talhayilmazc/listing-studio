"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { api } from "@/lib/api";
import {
  FIELD_GROUP,
  GROUPS,
  STYLE_LABELS,
  buildVariations,
  emptyValues,
  isOwn,
  list,
  optionsOf,
  refusal,
  shown,
  toNumber,
  withDefaults,
  type CategoryProperty,
  type Category,
  type FormOptions,
  type FormValues,
  type Personalization,
  type ProfileValues,
} from "@/lib/profileForm";
import type { Profile } from "@/lib/types";
import { Txt } from "./Txt";

export type FormMode =
  | { kind: "scratch"; shopId: string }
  | { kind: "copy"; profileId: string; shopId: string }
  | { kind: "edit"; profileId: string };

const POLL_MS = 2500;
const MAX_TRIES = 24;
type Group = (typeof GROUPS)[number];

/**
 * The profile form (feature/manual-profiles): a profile made from scratch for a shop, copied from another
 * profile, or edited. Five groups, each a plain list of fields with the shop's own Etsy options. Only values
 * Etsy would take are saved: a refusal is shown under its field in one sentence. On a profile that follows a
 * reference listing an edited field is marked "your value" (a Refresh never overwrites it), with "Use the
 * listing's value again" beside it; "Stop following the listing" makes the profile the seller's own.
 */
export function ProfileForm({ mode, onSaved, onClose }: { mode: FormMode; onSaved: (p: Profile) => void; onClose: () => void }) {
  const [loaded, setLoaded] = useState<ProfileValues | null>(null);
  const [options, setOptions] = useState<FormOptions | null>(null);
  const [name, setName] = useState("");
  const [values, setValues] = useState<FormValues>(emptyValues());
  const [style, setStyle] = useState("search");
  const [prefix, setPrefix] = useState("");
  const [tags, setTags] = useState("");
  const [materials, setMaterials] = useState("");
  const [personal, setPersonal] = useState<Personalization>({ enabled: false });
  const [path, setPath] = useState<string[]>([]);
  const [props, setProps] = useState<CategoryProperty[] | null>(null);
  const [varNames, setVarNames] = useState<string[]>(["", ""]);
  const [varOptions, setVarOptions] = useState<string[]>(["", ""]);
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [message, setMessage] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [open, setOpen] = useState<Group>("Basics");
  const [stopping, setStopping] = useState(false);
  const [waited, setWaited] = useState(false);

  const shopId = mode.kind === "edit" ? loaded?.connection_id ?? null : mode.shopId;
  const editing = mode.kind === "edit";
  const manual = loaded ? loaded.manual : true;
  const own = loaded?.own_fields ?? [];

  const fill = useCallback((v: ProfileValues) => {
    setLoaded(v);
    setName(v.name);
    const merged = { ...emptyValues(), ...v.values, quantity: v.values.quantity ?? (mode.kind === "edit" ? null : 999) } as FormValues;
    setValues(merged);
    setStyle(v.listing_style || "search");
    setPrefix(v.title_prefix ?? "");
    setTags((v.default_tags ?? []).join(", "));
    setMaterials((merged.materials ?? []).join(", "));
    setPersonal(v.personalization ?? { enabled: false });
    setPath(v.category_path ?? []);
    const table = merged.variations;
    setVarNames([table?.properties[0] ?? "", table?.properties[1] ?? ""]);
    const opts = optionsOf(table);
    setVarOptions([(opts[0] ?? []).join(", "), (opts[1] ?? []).join(", ")]);
  }, [mode.kind]);

  // The profile's values (edit, copy): asked again while its listing or the shops' lists are read.
  const tries = useRef(0);
  const read = useCallback(async () => {
    if (mode.kind === "scratch") return;
    try {
      const v = mode.kind === "edit" ? await api.profileValues(mode.profileId) : await api.profileCopyForm(mode.profileId, mode.shopId);
      if (v.reading && tries.current < MAX_TRIES) {
        tries.current += 1;
        setTimeout(read, POLL_MS);
        return;
      }
      if (v.reading) setWaited(true);
      fill(v);
    } catch (e: any) {
      setMessage(String(e.message ?? e));
    }
  }, [mode, fill]);
  useEffect(() => {
    read();
  }, [read]);

  // The shop's own lists.
  const optionTries = useRef(0);
  useEffect(() => {
    if (!shopId) return;
    let live = true;
    const ask = async () => {
      try {
        const o = await api.profileFormOptions(shopId);
        if (!live) return;
        if (o.reading && optionTries.current < MAX_TRIES) {
          optionTries.current += 1;
          setTimeout(ask, POLL_MS);
          return;
        }
        if (o.reading) setWaited(true);
        setOptions(o);
        if (!o.reading && mode.kind === "scratch") setValues((v) => withDefaults(v, o));
      } catch (e: any) {
        if (live) setMessage(String(e.message ?? e));
      }
    };
    ask();
    return () => {
      live = false;
    };
  }, [shopId, mode.kind]);

  // The chosen category's attributes.
  const taxonomy = values.taxonomy_id;
  useEffect(() => {
    if (!shopId || !taxonomy) {
      setProps(null);
      return;
    }
    let live = true;
    let n = 0;
    const ask = async () => {
      try {
        const c = await api.profileCategory(shopId, taxonomy);
        if (!live) return;
        if (c.path.length) setPath(c.path);
        if (c.reading && n < MAX_TRIES) {
          n += 1;
          setTimeout(ask, POLL_MS);
          return;
        }
        setProps(c.reading ? null : c.properties);
      } catch {
        if (live) setProps(null);
      }
    };
    ask();
    return () => {
      live = false;
    };
  }, [shopId, taxonomy]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const set = <K extends keyof FormValues>(key: K, value: FormValues[K]) => setValues((v) => ({ ...v, [key]: value }));

  async function save() {
    if (!shopId) return;
    setBusy(true);
    setMessage(null);
    setErrors({});
    const body = {
      name, values: { ...values, materials: list(materials) }, listing_style: style, title_prefix: prefix,
      default_tags: list(tags), personalization: personal,
    };
    try {
      const saved = mode.kind === "edit"
        ? await api.saveProfileValues(mode.profileId, body)
        : await api.createProfileFromForm({ ...body, connection_id: shopId, copied_from: mode.kind === "copy" ? mode.profileId : null });
      onSaved(saved);
      onClose();
    } catch (e) {
      const r = refusal(e);
      setErrors(r.fields);
      setMessage(r.message);
      const first = Object.keys(r.fields)[0];
      if (first && FIELD_GROUP[first]) setOpen(FIELD_GROUP[first]);
    } finally {
      setBusy(false);
    }
  }

  async function useListing(field: string) {
    if (mode.kind !== "edit") return;
    setBusy(true);
    try {
      onSaved(await api.useListingValue(mode.profileId, field));
      fill(await api.profileValues(mode.profileId));
    } catch (e: any) {
      setMessage(String(e.message ?? e));
    } finally {
      setBusy(false);
    }
  }

  async function stopFollowing() {
    if (mode.kind !== "edit") return;
    setBusy(true);
    try {
      onSaved(await api.stopFollowingListing(mode.profileId));
      setStopping(false);
      fill(await api.profileValues(mode.profileId));
    } catch (e: any) {
      setMessage(String(e.message ?? e));
    } finally {
      setBusy(false);
    }
  }

  /** Under a field of a profile that follows a listing: whose value it is. */
  const whose = (field: string) => {
    if (!editing || manual) return null;
    if (!isOwn(field, own, manual)) return <span key="follows" className="text-[11px] text-slate-500">follows the listing</span>;
    return (
      <span key="own" className="flex flex-wrap items-center gap-x-2 text-[11px]" data-testid={`own-${field}`}>
        <span className="rounded bg-amber-100 px-1.5 py-0.5 font-medium text-amber-900">your value</span>
        <span className="text-slate-600" translate="no">{`the listing's: ${shown(field, (loaded?.listing_values as any)?.[field], options)}`}</span>
        <button type="button" className="tap text-brand-700 underline" disabled={busy} onClick={() => useListing(field)}
          data-testid={`use-listing-${field}`}>
          Use the listing&apos;s value again
        </button>
      </span>
    );
  };
  const bad = (field: string) =>
    errors[field] ? <span key="bad" role="alert" className="text-xs text-rose-700" data-testid={`error-${field}`}><Txt>{errors[field]}</Txt></span> : null;

  const title = mode.kind === "scratch" ? "New profile from scratch" : mode.kind === "copy" ? "Copy a profile" : "Edit profile";
  const shopName = options?.shop_name ?? loaded?.shop_name ?? "";
  const ready = mode.kind === "scratch" ? options !== null : loaded !== null && !loaded.reading;
  const calculated = options?.shipping.find((o) => o.id === values.shipping_profile_id)?.type === "calculated";
  const table = values.variations;

  return createPortal(
    <div className="fixed inset-0 z-50 flex items-start justify-center bg-black/40 sm:p-6" onClick={onClose}>
      <div role="dialog" aria-modal="true" aria-label={title} data-testid="profile-form"
        className="flex h-full w-full max-w-3xl flex-col overflow-y-auto bg-white p-4 text-sm shadow-xl sm:h-auto sm:max-h-[92vh] sm:rounded-lg"
        onClick={(e) => e.stopPropagation()}>
        <div className="mb-3 flex items-start justify-between gap-3">
          <div className="min-w-0">
            <h2 className="font-display text-lg text-slate-900">{title}</h2>
            <p className="text-xs text-slate-600">
              {shopName ? <span key="shop" translate="no">{`For ${shopName}. `}</span> : null}
              <span>
                {editing && !manual
                  ? "Change any value: it becomes your value and a Refresh never overwrites it."
                  : "Every value is your own: nothing is read from a listing, so it never needs a refresh."}
              </span>
            </p>
          </div>
          <button type="button" className="btn-secondary shrink-0 px-3 py-1.5 text-xs" onClick={onClose}>Close</button>
        </div>

        {!ready ? (
          <p key="loading" className="text-slate-600" translate="no" data-testid="profile-form-reading">
            {waited ? "Still reading from Etsy. Close this and try again in a minute." : "Reading the shop's settings from Etsy…"}
          </p>
        ) : (
          <div key="form" className="space-y-2">
            {(loaded?.notes ?? []).map((n) => <p key={n} className="rounded-md bg-slate-50 p-2 text-xs text-slate-700"><Txt>{n}</Txt></p>)}
            {(loaded?.asked ?? []).length > 0 && (
              <ul key="asked" className="space-y-1 rounded-md border border-amber-300 bg-amber-50 p-2 text-xs text-amber-950" data-testid="copy-asked">
                {loaded!.asked.map((a) => <li key={a.field}><Txt>{a.reason}</Txt></li>)}
              </ul>
            )}
            {options?.reading && <p key="opts" className="text-xs text-amber-800">This shop&apos;s lists could not be read yet; its settings can be chosen once they are.</p>}

            <Section name="Basics" open={open} setOpen={setOpen} errors={errors}>
              <Field label="Profile name" foot={bad("name")}>
                <input className="field" value={name} maxLength={120} onChange={(e) => setName(e.target.value)} aria-label="Profile name" data-testid="form-name" />
              </Field>
              <Field label="Category" foot={<>{whose("taxonomy_id")}{bad("taxonomy_id")}</>}>
                <CategoryPicker shopId={shopId!} path={path} onPick={(c) => { set("taxonomy_id", c.id); setPath(c.path); }} />
              </Field>
              <div className="grid gap-3 sm:grid-cols-3">
                <Field label="Who made it" foot={<>{whose("who_made")}{bad("who_made")}</>}>
                  <select className="field" value={values.who_made ?? ""} onChange={(e) => set("who_made", e.target.value)} aria-label="Who made it">
                    {(options?.who_made ?? []).map((w) => <option key={w.value} value={w.value}>{w.label}</option>)}
                  </select>
                </Field>
                <Field label="When it was made" foot={<>{whose("when_made")}{bad("when_made")}</>}>
                  <select className="field" value={values.when_made ?? ""} onChange={(e) => set("when_made", e.target.value)} aria-label="When it was made">
                    {(options?.when_made ?? []).map((w) => <option key={w.value} value={w.value}>{w.label}</option>)}
                  </select>
                </Field>
                <Field label="What it is" foot={<>{whose("is_supply")}{bad("is_supply")}</>}>
                  <select className="field" value={values.is_supply ? "supply" : "finished"} onChange={(e) => set("is_supply", e.target.value === "supply")} aria-label="What it is">
                    <option value="finished">A finished product</option>
                    <option value="supply">A supply or tool to make things</option>
                  </select>
                </Field>
              </div>
              <div className="grid gap-3 sm:grid-cols-2">
                <Field label="Title style" foot={bad("listing_style")}>
                  <select className="field" value={style} onChange={(e) => setStyle(e.target.value)} aria-label="Title style">
                    {Object.entries(STYLE_LABELS).map(([k, l]) => <option key={k} value={k}>{l}</option>)}
                  </select>
                </Field>
                <Field label="Title prefix (optional)" foot={bad("title_prefix")}>
                  <input className="field" value={prefix} maxLength={60} onChange={(e) => setPrefix(e.target.value)} aria-label="Title prefix" placeholder="None" />
                </Field>
              </div>
            </Section>

            <Section name="Price & variations" open={open} setOpen={setOpen} errors={errors}>
              <div className="grid gap-3 sm:grid-cols-2">
                <Field label={`Price${options?.currency ? ` (${options.currency})` : ""}`} foot={<>{whose("price")}{bad("price")}</>}>
                  <input className="field" inputMode="decimal" value={values.price ?? ""} onChange={(e) => set("price", toNumber(e.target.value))}
                    aria-label="Price" data-testid="form-price" />
                </Field>
                <Field label="Quantity" foot={<>{whose("quantity")}{bad("quantity")}</>}>
                  <input className="field" inputMode="numeric" value={values.quantity ?? ""} onChange={(e) => set("quantity", toNumber(e.target.value))}
                    aria-label="Quantity" placeholder="999" />
                </Field>
              </div>
              <div className="space-y-2 rounded-md border border-slate-200 p-3" data-testid="form-variations">
                <p className="font-medium text-slate-800">Variations (optional)</p>
                <p className="text-xs text-slate-600">Up to two, each with its options separated by commas. Every combination gets its own price and quantity.</p>
                {[0, 1].map((i) => (
                  <div key={i} className="grid gap-2 sm:grid-cols-[10rem_minmax(0,1fr)]">
                    <input className="field" value={varNames[i]} placeholder={i === 0 ? "Size" : "Colour"} aria-label={`Variation ${i + 1} name`}
                      onChange={(e) => setVarNames((n) => n.map((x, k) => (k === i ? e.target.value : x)))} data-testid={`variation-name-${i}`} />
                    <input className="field" value={varOptions[i]} placeholder={i === 0 ? "S, M, L, XL" : "Black, White"} aria-label={`Variation ${i + 1} options`}
                      onChange={(e) => setVarOptions((n) => n.map((x, k) => (k === i ? e.target.value : x)))} data-testid={`variation-options-${i}`} />
                  </div>
                ))}
                <div className="flex flex-wrap gap-2">
                  <button type="button" className="btn-secondary px-3 py-1.5 text-xs" data-testid="variation-make"
                    onClick={() => set("variations", buildVariations(varNames, varOptions.map(list), table, values.price, values.quantity))}>
                    {table ? "Update the options" : "Make the options"}
                  </button>
                  {table && (
                    <button key="none" type="button" className="btn-quiet px-3 py-1.5 text-xs" onClick={() => { set("variations", null); setVarNames(["", ""]); setVarOptions(["", ""]); }}>
                      Remove variations
                    </button>
                  )}
                </div>
                {table && table.rows.length > 0 && (
                  <div key="rows" className="overflow-x-auto">
                    <table className="w-full min-w-[22rem] text-left text-xs">
                      <thead className="text-slate-600">
                        <tr>
                          <th className="py-1 pr-2 font-medium"><span translate="no">{table.properties.join(" / ")}</span></th>
                          <th className="py-1 pr-2 font-medium">Price</th>
                          <th className="py-1 pr-2 font-medium">Quantity</th>
                          <th className="py-1 font-medium">Sold</th>
                        </tr>
                      </thead>
                      <tbody>
                        {table.rows.map((row, i) => {
                          const label = row.values.join(" / ");
                          const patch = (change: Partial<typeof row>) =>
                            set("variations", { ...table, rows: table.rows.map((r, k) => (k === i ? { ...r, ...change } : r)) });
                          return (
                            <tr key={label} className="border-t border-slate-100" data-testid="variation-row">
                              <td className="py-1 pr-2"><span translate="no">{label}</span></td>
                              <td className="py-1 pr-2">
                                <input className="field w-24 py-1" inputMode="decimal" value={row.price ?? ""} aria-label={`Price of ${label}`}
                                  onChange={(e) => patch({ price: toNumber(e.target.value) })} data-testid="variation-price" />
                              </td>
                              <td className="py-1 pr-2">
                                <input className="field w-20 py-1" inputMode="numeric" value={row.quantity ?? ""} aria-label={`Quantity of ${label}`}
                                  onChange={(e) => patch({ quantity: toNumber(e.target.value) })} />
                              </td>
                              <td className="py-1">
                                <input type="checkbox" checked={row.enabled} aria-label={`${label} is sold`} onChange={(e) => patch({ enabled: e.target.checked })} />
                              </td>
                            </tr>
                          );
                        })}
                      </tbody>
                    </table>
                  </div>
                )}
                <div className="flex flex-col gap-1">{whose("variations")}{bad("variations")}</div>
              </div>
            </Section>

            <Section name="Shipping & policies" open={open} setOpen={setOpen} errors={errors}>
              {([["shipping_profile_id", "Shipping profile", options?.shipping], ["return_policy_id", "Return policy", options?.returns],
                 ["readiness_state_id", "Processing profile", options?.readiness]] as const).map(([field, label, items]) => (
                <Field key={field} label={label} foot={<>{whose(field)}{bad(field)}</>}>
                  <select className="field" value={values[field] ?? ""} aria-label={label} data-testid={`form-${field}`}
                    onChange={(e) => set(field, e.target.value ? Number(e.target.value) : null)}>
                    <option value="">Choose…</option>
                    {(items ?? []).map((o) => <option key={o.id} value={o.id}>{o.label}</option>)}
                  </select>
                  {(items ?? []).length === 0 && !options?.reading && (
                    <span key="none" className="text-xs text-amber-800" translate="no">
                      {`${shopName || "This shop"} has no ${label.toLowerCase()} on Etsy yet. Make one in Shop Manager, then open this form again.`}
                    </span>
                  )}
                </Field>
              ))}
              <Field label="Production partner" foot={<>{whose("production_partner_ids")}{bad("production_partner_ids")}</>}>
                {(options?.partners ?? []).length > 0 ? (
                  <select key="pick" className="field" value={values.production_partner_ids[0] ?? ""} aria-label="Production partner" data-testid="form-partner"
                    onChange={(e) => set("production_partner_ids", e.target.value ? [Number(e.target.value)] : [])}>
                    <option value="">None</option>
                    {options!.partners.map((o) => <option key={o.id} value={o.id}>{o.label}</option>)}
                  </select>
                ) : (
                  <p key="note" className="text-xs text-amber-800" data-testid="form-partner-note">
                    <Txt>{loaded?.partner_note ?? options?.partner_note ?? "This shop has no production partner on Etsy."}</Txt>
                  </p>
                )}
              </Field>
              {(calculated || values.item_weight != null || errors.item_weight || errors.item_length) && (
                <div key="size" className="grid gap-3 sm:grid-cols-2">
                  <Field label="Weight" foot={bad("item_weight")}>
                    <span className="flex gap-2">
                      <input className="field min-w-0 flex-1" inputMode="decimal" value={values.item_weight ?? ""} aria-label="Weight"
                        onChange={(e) => set("item_weight", toNumber(e.target.value))} />
                      <select className="field w-20" value={values.item_weight_unit ?? "oz"} aria-label="Weight unit" onChange={(e) => set("item_weight_unit", e.target.value)}>
                        {["oz", "lb", "g", "kg"].map((u) => <option key={u} value={u}>{u}</option>)}
                      </select>
                    </span>
                  </Field>
                  <Field label="Packed size (length × width × height)" foot={bad("item_length")}>
                    <span className="flex gap-2">
                      {(["item_length", "item_width", "item_height"] as const).map((k) => (
                        <input key={k} className="field min-w-0 flex-1" inputMode="decimal" value={values[k] ?? ""} aria-label={k.replace("item_", "")}
                          onChange={(e) => set(k, toNumber(e.target.value))} />
                      ))}
                      <select className="field w-20" value={values.item_dimensions_unit ?? "in"} aria-label="Size unit" onChange={(e) => set("item_dimensions_unit", e.target.value)}>
                        {["in", "cm"].map((u) => <option key={u} value={u}>{u}</option>)}
                      </select>
                    </span>
                  </Field>
                </div>
              )}
            </Section>

            <Section name="Details" open={open} setOpen={setOpen} errors={errors}>
              <Field label="Materials (optional, separated by commas)" foot={<>{whose("materials")}{bad("materials")}</>}>
                <input className="field" value={materials} onChange={(e) => setMaterials(e.target.value)} aria-label="Materials" placeholder="Cotton, Polyester" data-testid="form-materials" />
              </Field>
              <div className="space-y-2">
                <p className="font-medium text-slate-800">Attributes</p>
                {!values.taxonomy_id ? (
                  <p key="first" className="text-xs text-slate-600">Choose a category first: its attributes are Etsy&apos;s own lists.</p>
                ) : props === null ? (
                  <p key="reading" className="text-xs text-slate-600">Reading the category&apos;s attributes from Etsy…</p>
                ) : (
                  <div key="props" className="grid gap-2 sm:grid-cols-2" data-testid="form-attributes">
                    {[...props].filter((p) => p.kind !== "number").sort((a, b) => Number(b.required) - Number(a.required)).slice(0, 40).map((p) => {
                      const raw = values.attributes[p.name];
                      const chosen = Array.isArray(raw) ? raw : raw?.values ?? [];
                      const put = (next: string[]) => {
                        const rest = Object.fromEntries(Object.entries(values.attributes).filter(([k]) => k !== p.name));
                        set("attributes", next.length ? { ...rest, [p.name]: next } : rest);
                      };
                      return (
                        <label key={p.name} className="flex flex-col gap-1 text-xs text-slate-700">
                          <span translate="no">{p.required ? `${p.name} (Etsy requires it)` : p.name}</span>
                          <select className="field py-1.5 text-sm" value={chosen[0] ?? ""} aria-label={p.name} onChange={(e) => put(e.target.value ? [e.target.value] : [])}>
                            <option value="">Not set</option>
                            {p.values.map((v) => <option key={v.name} value={v.name}>{v.name}</option>)}
                          </select>
                        </label>
                      );
                    })}
                  </div>
                )}
                <div className="flex flex-col gap-1">{whose("attributes")}{bad("attributes")}</div>
              </div>
              <div className="space-y-2 rounded-md border border-slate-200 p-3">
                <label className="flex min-h-[2.75rem] items-center gap-2 font-medium text-slate-800">
                  <input type="checkbox" checked={personal.enabled} onChange={(e) => setPersonal((p) => ({ ...p, enabled: e.target.checked }))} data-testid="form-personalization" />
                  <span>Buyers can personalize it</span>
                </label>
                {personal.enabled && (
                  <div key="p" className="grid gap-2">
                    <input className="field" value={personal.question_text ?? ""} maxLength={45} placeholder="Personalization" aria-label="Personalization question"
                      onChange={(e) => setPersonal((p) => ({ ...p, question_text: e.target.value }))} />
                    <textarea className="field min-h-[4.5rem]" value={personal.instructions ?? ""} placeholder="What the buyer should type (120 characters at most)"
                      aria-label="Personalization instructions" onChange={(e) => setPersonal((p) => ({ ...p, instructions: e.target.value }))} />
                    <div className="flex flex-wrap items-center gap-4 text-xs">
                      <label className="flex min-h-[2.75rem] items-center gap-2">
                        <input type="checkbox" checked={!!personal.required} onChange={(e) => setPersonal((p) => ({ ...p, required: e.target.checked }))} />
                        <span>Required</span>
                      </label>
                      <label className="flex items-center gap-2">
                        <span>Character limit</span>
                        <input className="field w-24 py-1.5" inputMode="numeric" value={personal.max_allowed_characters ?? ""} aria-label="Character limit" placeholder="256"
                          onChange={(e) => setPersonal((p) => ({ ...p, max_allowed_characters: toNumber(e.target.value) }))} />
                      </label>
                    </div>
                  </div>
                )}
                {bad("personalization")}
              </div>
            </Section>

            <Section name="Text" open={open} setOpen={setOpen} errors={errors}>
              <Field label="Description (every listing made with the profile ends with it)" foot={<>{whose("description")}{bad("description")}</>}>
                <textarea className="field min-h-[9rem]" value={values.description} onChange={(e) => set("description", e.target.value)} aria-label="Description" data-testid="form-description" />
              </Field>
              <Field label="Default tags (optional, separated by commas; 13 at most, 20 characters each)" foot={bad("default_tags")}>
                <input className="field" value={tags} onChange={(e) => setTags(e.target.value)} aria-label="Default tags" placeholder="gift for her, soft tee" data-testid="form-tags" />
              </Field>
            </Section>

            {message && <p key="message" role="alert" className="text-sm text-rose-700" data-testid="profile-form-message"><Txt>{message}</Txt></p>}
            <div className="flex flex-wrap items-center gap-2 border-t border-slate-100 pt-3">
              <button type="button" className="btn-primary" onClick={save} disabled={busy || !name.trim()} data-testid="profile-form-save">
                {busy ? "Saving…" : editing ? "Save" : "Create the profile"}
              </button>
              <button type="button" className="btn-secondary" onClick={onClose}>Cancel</button>
              {editing && !manual && !stopping && (
                <button key="stop" type="button" className="btn-quiet ml-auto text-xs" onClick={() => setStopping(true)} data-testid="stop-following">
                  Stop following the listing
                </button>
              )}
            </div>
            {stopping && (
              <div key="stopping" className="space-y-2 rounded-md border border-amber-300 bg-amber-50 p-3 text-xs text-amber-950" data-testid="stop-following-confirm">
                <p>
                  The profile keeps every value it has now as your own and is never refreshed from the listing again. Its size chart images were the
                  listing&apos;s: they are no longer added to new drafts. This cannot be undone.
                </p>
                <div className="flex flex-wrap gap-2">
                  <button type="button" className="btn-danger px-3 py-1.5 text-xs" onClick={stopFollowing} disabled={busy} data-testid="stop-following-yes">Stop following the listing</button>
                  <button type="button" className="btn-secondary px-3 py-1.5 text-xs" onClick={() => setStopping(false)}>Keep following</button>
                </div>
              </div>
            )}
          </div>
        )}
      </div>
    </div>,
    document.body,
  );
}

function Section({ name, open, setOpen, errors, children }: {
  name: Group; open: Group; setOpen: (g: Group) => void; errors: Record<string, string>; children: React.ReactNode;
}) {
  const wrong = Object.keys(errors).filter((f) => FIELD_GROUP[f] === name).length;
  const isOpen = open === name;
  return (
    <section className="rounded-md border border-slate-200" data-testid={`form-group-${name}`}>
      <button type="button" className="flex min-h-[2.75rem] w-full items-center justify-between gap-2 px-3 py-2 text-left font-medium text-slate-900"
        aria-expanded={isOpen} onClick={() => setOpen(name)}>
        <span>{name}</span>
        {wrong > 0 ? <span key="wrong" className="text-xs font-normal text-rose-700" translate="no">{`${wrong} to fix`}</span> : null}
      </button>
      {/* Kept mounted: what was typed in a closed group is still there, and still sent. */}
      <div className="space-y-3 border-t border-slate-100 p-3" hidden={!isOpen}>{children}</div>
    </section>
  );
}

function Field({ label, foot, children }: { label: string; foot?: React.ReactNode; children: React.ReactNode }) {
  return (
    <div className="flex flex-col gap-1">
      <span className="text-xs font-medium text-slate-700"><Txt>{label}</Txt></span>
      {children}
      <div className="flex flex-col gap-1">{foot}</div>
    </div>
  );
}

/** A category of Etsy's, found by typing a few words of its name. */
function CategoryPicker({ shopId, path, onPick }: { shopId: string; path: string[]; onPick: (c: Category) => void }) {
  const [q, setQ] = useState("");
  const [found, setFound] = useState<Category[] | null>(null);
  const [reading, setReading] = useState(false);
  useEffect(() => {
    if (q.trim().length < 2) {
      setFound(null);
      return;
    }
    let live = true;
    let n = 0;
    const ask = async () => {
      try {
        const res = await api.profileCategories(shopId, q.trim());
        if (!live) return;
        if (res.reading && n < MAX_TRIES) {
          n += 1;
          setReading(true);
          setTimeout(ask, POLL_MS);
          return;
        }
        setReading(false);
        setFound(res.categories);
      } catch {
        if (live) setFound([]);
      }
    };
    const t = setTimeout(ask, 250);
    return () => {
      live = false;
      clearTimeout(t);
    };
  }, [q, shopId]);
  return (
    <div className="space-y-1" data-testid="form-category">
      {path.length > 0 && <p key="path" className="text-sm text-slate-900" translate="no" data-testid="form-category-chosen">{path.join(" › ")}</p>}
      <input className="field" value={q} onChange={(e) => setQ(e.target.value)} aria-label="Search Etsy's categories"
        placeholder={path.length ? "Search to change the category" : "Search Etsy's categories: t-shirt, mug, tote…"} data-testid="form-category-search" />
      {reading && <p key="reading" className="text-xs text-slate-600">Reading Etsy&apos;s categories…</p>}
      {found !== null && !reading && (
        found.length === 0 ? (
          <p key="none" className="text-xs text-slate-600">No category has those words. Try fewer, or another word.</p>
        ) : (
          <ul key="found" className="max-h-56 overflow-y-auto rounded-md border border-slate-200">
            {found.map((c) => (
              <li key={c.id}>
                <button type="button" className="tap block w-full px-2 py-2 text-left text-xs hover:bg-slate-50" data-testid="form-category-option"
                  onClick={() => { onPick(c); setQ(""); setFound(null); }}>
                  <span translate="no">{c.path.join(" › ")}</span>
                </button>
              </li>
            ))}
          </ul>
        )
      )}
    </div>
  );
}
