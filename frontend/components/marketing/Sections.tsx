import { Check, Photo, Spark } from "@/components/marketing/HeroDemo";
import { PlanCards } from "@/components/marketing/PlanCards";
import { HOME } from "@/lib/homeCopy";
import { initials } from "@/lib/marketing/testimonials";

/**
 * The home page below the hero, section by section as the reference draws them
 * (docs/design/home-reference.dc.html): the proof strip, the grind, four steps, every shop's own version,
 * safety, what sellers say, pricing, questions and the closing call. Every word comes from lib/homeCopy.ts;
 * prices and the cost of a listing from lib/plans.ts, the customers' words from lib/marketing/testimonials.ts.
 */

const H2 = { margin: 0, fontSize: "clamp(36px, 4.4vw, 58px)", lineHeight: 1.0, fontWeight: 900, fontStretch: "110%", letterSpacing: "-1.6px" } as const;
const SUB = { margin: 0, fontSize: 19, lineHeight: 1.5, color: "#4B4F6B" } as const;

/** A step's number and title: the number in muted text (4.9:1; the reference's #9A9FB8 is 2.6:1). */
function StepTitle({ n, title }: { n: string; title: string }) {
  return (
    <div style={{ display: "flex", gap: 12, alignItems: "baseline" }}>
      <span style={{ fontSize: 16, fontWeight: 900, color: "#6A6F8A" }}>{n}</span>
      <h3 style={{ margin: 0, fontSize: 22, fontWeight: 850 }}>{title}</h3>
    </div>
  );
}

export function Strip() {
  return (
    <section style={{ borderTop: "1px solid #EEF0F5", borderBottom: "1px solid #EEF0F5", background: "#FAFBFD" }} aria-label="What you can count on">
      <div className="lx-wrap" style={{ display: "flex", flexWrap: "wrap", justifyContent: "space-between", gap: "18px 32px", paddingTop: 26, paddingBottom: 26, fontSize: 15, fontWeight: 700, color: "#3B3F5C" }}>
        {HOME.strip.map((item) => (
          <span key={item} style={{ display: "inline-flex", gap: 10, alignItems: "center" }}>
            <span className="lx-cb" style={{ display: "inline-flex", width: 30, height: 30, borderRadius: 9, background: "#FFFFFF", border: "1px solid #E6E8EF", alignItems: "center", justifyContent: "center" }}>
              <svg width="14" height="14" viewBox="0 0 14 14" aria-hidden="true"><path d="M2.6 7.2 L5.6 10 L11.4 4" stroke="#14162B" strokeWidth="2" fill="none" strokeLinecap="round" strokeLinejoin="round" /></svg>
            </span>
            <span>{item}</span>
          </span>
        ))}
      </div>
    </section>
  );
}

export function Grind() {
  const g = HOME.grind;
  return (
    <section style={{ padding: "120px 0 110px" }} aria-labelledby="grind-title">
      <div className="lx-wrap" style={{ display: "flex", flexDirection: "column", gap: 52 }}>
        <div style={{ display: "flex", flexDirection: "column", gap: 16, maxWidth: 760 }}>
          <h2 id="grind-title" style={H2}>{g.heading}</h2>
          <p style={SUB}>{g.sub}</p>
        </div>
        <div className="lx-two">
          <div style={{ borderRadius: 28, padding: 36, background: "#F5F6FA", display: "flex", flexDirection: "column", gap: 20 }}>
            <span style={{ fontSize: 15, fontWeight: 800, color: "#6A6F8A" }}>{g.byHandLabel}</span>
            {g.byHand.map((item) => (
              <span key={item} style={{ display: "flex", gap: 14, alignItems: "center", fontSize: 18, fontWeight: 600, color: "#6A6F8A" }}>
                <svg width="22" height="22" viewBox="0 0 22 22" aria-hidden="true" style={{ flexShrink: 0 }}><circle cx="11" cy="11" r="9" fill="none" stroke="#B9BDCE" strokeWidth="2" /><path d="M11 6 V11 L14.5 13" stroke="#B9BDCE" strokeWidth="2" fill="none" strokeLinecap="round" /></svg>
                <span style={{ textDecoration: "line-through", textDecorationColor: "rgba(106,111,138,0.5)" }}>{item}</span>
              </span>
            ))}
          </div>
          <div className="lx-dark" style={{ borderRadius: 28, padding: 36, background: "#14162B", color: "#FFFFFF", display: "flex", flexDirection: "column", gap: 22, boxShadow: "0 30px 70px rgba(20,22,43,0.25)" }}>
            <span style={{ fontSize: 15, fontWeight: 800, color: "#FFD24A" }}>{g.withLabel}</span>
            {g.withUs.map((item) => (
              <span key={item} style={{ display: "flex", gap: 16, alignItems: "center", fontSize: 26, fontWeight: 850, fontStretch: "106%", lineHeight: 1.2 }}>
                <span style={{ display: "inline-flex", flexShrink: 0, width: 38, height: 38, borderRadius: 99, background: "#FFD24A", alignItems: "center", justifyContent: "center" }}>
                  <svg width="16" height="16" viewBox="0 0 16 16" aria-hidden="true"><path d="M3.2 8.4 L6.6 11.4 L12.8 4.8" stroke="#14162B" strokeWidth="2.6" fill="none" strokeLinecap="round" strokeLinejoin="round" /></svg>
                </span>
                <span>{item}</span>
              </span>
            ))}
            <span style={{ marginTop: "auto", fontSize: 17, lineHeight: 1.5, color: "#B7BBD3" }}>{g.note}</span>
          </div>
        </div>
      </div>
    </section>
  );
}

export function How() {
  const h = HOME.how;
  const [s1, s2, s3, s4] = h.steps;
  const step = { display: "flex", flexDirection: "column", gap: 16 } as const;
  const text = { margin: 0, fontSize: 16, lineHeight: 1.5, color: "#4B4F6B" } as const;
  return (
    <section id={h.id} style={{ padding: "20px 0 120px", scrollMarginTop: 20 }} aria-labelledby="how-title">
      <div className="lx-wrap" style={{ display: "flex", flexDirection: "column", gap: 48 }}>
        <h2 id="how-title" style={{ ...H2, maxWidth: "14em" }}>{h.heading}</h2>
        <div className="lx-steps">
          <div style={step}>
            <div style={{ borderRadius: 24, background: "#EEF3FB", height: 230, padding: 24, boxSizing: "border-box", display: "flex", flexDirection: "column", justifyContent: "center", gap: 9 }}>
              {h.files.map((f) => (
                <span key={f.n} style={{ display: "flex", gap: 12, alignItems: "center", background: "#FFFFFF", borderRadius: 12, padding: "10px 12px", fontSize: 13.5, fontWeight: 700, boxShadow: "0 2px 6px rgba(20,22,43,0.06)" }}>
                  <span style={{ width: 22, height: 22, borderRadius: 6, background: f.c, flexShrink: 0 }} />
                  <span>{f.n}</span>
                </span>
              ))}
            </div>
            <StepTitle n={s1.n} title={s1.title} />
            <p style={text}>{s1.text}</p>
          </div>

          <div style={step}>
            <div style={{ borderRadius: 24, background: "#FFF3D1", height: 230, padding: "20px 18px", boxSizing: "border-box", display: "grid", gridTemplateColumns: "repeat(3, minmax(0, 1fr))", gap: "6px 4px", alignContent: "center" }} data-testid="step-colours">
              {h.colours.map((c) => (
                <div key={c.alt} style={{ borderRadius: 10, overflow: "hidden" }}>
                  <Photo shot={c.shot} alt={c.alt} />
                </div>
              ))}
            </div>
            <StepTitle n={s2.n} title={s2.title} />
            <p style={text}>{s2.text}</p>
          </div>

          <div style={step}>
            <div style={{ borderRadius: 24, background: "#EAF6F0", height: 230, padding: 22, boxSizing: "border-box", display: "flex", flexDirection: "column", justifyContent: "center", gap: 10 }}>
              <span style={{ display: "inline-flex", gap: 7, alignItems: "center", fontSize: 12, fontWeight: 800, color: "#1A7340" }}>
                <Spark size={14} colour="#1F8A4C" />
                <span>{h.written}</span>
              </span>
              <span style={{ background: "#FFFFFF", borderRadius: 12, padding: 12, fontSize: 13.5, fontWeight: 800, lineHeight: 1.3, boxShadow: "0 2px 6px rgba(20,22,43,0.06)" }}>{h.writtenTitle}</span>
              <div style={{ display: "flex", flexWrap: "wrap", gap: 5 }}>
                {h.tagsShort.map((tag) => (
                  <span key={tag} style={{ fontSize: 11.5, fontWeight: 700, background: "#FFFFFF", borderRadius: 7, padding: "4px 8px" }}>{tag}</span>
                ))}
              </div>
            </div>
            <StepTitle n={s3.n} title={s3.title} />
            <p style={text}>{s3.text}</p>
          </div>

          <div style={step}>
            <div style={{ borderRadius: 24, background: "#FBEDEF", height: 230, padding: 22, boxSizing: "border-box", display: "flex", flexDirection: "column", justifyContent: "center", gap: 12 }}>
              <div style={{ display: "grid", gridTemplateColumns: "repeat(7, minmax(0, 1fr))", gap: 6 }}>
                {h.days.map((d, i) => (
                  <span key={i} style={{ display: "flex", flexDirection: "column", gap: 5, alignItems: "center" }}>
                    <span style={{ fontSize: 11, fontWeight: 800, color: "#4B4F6B" }}>{d.n}</span>
                    <span style={{ width: "100%", height: 24, borderRadius: 7, background: d.a }} />
                    <span style={{ width: "100%", height: 24, borderRadius: 7, background: d.b }} />
                    <span style={{ width: "100%", height: 24, borderRadius: 7, background: d.c }} />
                  </span>
                ))}
              </div>
              <span style={{ display: "flex", gap: 14, fontSize: 12, fontWeight: 700, color: "#4B4F6B" }}>
                {h.legend.map((l) => (
                  <span key={l.name} style={{ display: "inline-flex", gap: 6, alignItems: "center" }}>
                    <span style={{ width: 8, height: 8, borderRadius: 99, background: l.dot }} />
                    <span>{l.name}</span>
                  </span>
                ))}
              </span>
            </div>
            <StepTitle n={s4.n} title={s4.title} />
            <p style={text}>{s4.text}</p>
          </div>
        </div>
      </div>
    </section>
  );
}

export function ShopVersions() {
  const s = HOME.shops;
  return (
    <section className="lx-dark" style={{ background: "#14162B", color: "#FFFFFF", padding: "120px 0", backgroundImage: "radial-gradient(700px 400px at 90% 0%, rgba(255,210,74,0.16), rgba(255,210,74,0) 70%), radial-gradient(600px 400px at 0% 100%, rgba(150,120,255,0.18), rgba(150,120,255,0) 70%)" }} aria-labelledby="shops-title">
      <div className="lx-wrap" style={{ display: "flex", flexDirection: "column", gap: 52 }}>
        <div style={{ display: "flex", flexWrap: "wrap", gap: "24px 56px", alignItems: "flex-end", justifyContent: "space-between" }}>
          <h2 id="shops-title" style={{ ...H2, maxWidth: "12em", color: "#FFFFFF" }}>{s.heading}</h2>
          <p style={{ margin: 0, fontSize: 19, lineHeight: 1.5, color: "#C3C7DD", maxWidth: "25em" }}>{s.sub}</p>
        </div>
        <div className="lx-shops" data-testid="shop-cards">
          {s.cards.map((c) => (
            <div key={c.shop} style={{ borderRadius: 24, padding: 22, background: "rgba(255,255,255,0.06)", border: "1px solid rgba(255,255,255,0.10)", display: "flex", flexDirection: "column", gap: 16 }}>
              <span style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                <span style={{ display: "inline-flex", gap: 9, alignItems: "center", fontSize: 16, fontWeight: 800 }}>
                  <span style={{ width: 10, height: 10, borderRadius: 99, background: c.dot }} />
                  <span>{c.shop}</span>
                </span>
                <span style={{ fontSize: 13, color: "#A4A9C4" }}>{c.when}</span>
              </span>
              <div style={{ borderRadius: 18, background: "linear-gradient(180deg, #2A2D48, #23263F)", overflow: "hidden" }}>
                <ShopPhoto shot={c.shot} alt={c.alt} />
              </div>
              <span style={{ fontSize: 16, fontWeight: 700, lineHeight: 1.35 }}>{c.title}</span>
              <span style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
                <span style={{ fontSize: 12, fontWeight: 800, background: "rgba(255,255,255,0.10)", borderRadius: 7, padding: "5px 9px" }} translate="no">{`SKU ${c.sku}`}</span>
                <span style={{ fontSize: 12, fontWeight: 800, background: "rgba(255,255,255,0.10)", borderRadius: 7, padding: "5px 9px" }}>{c.extra}</span>
              </span>
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}

/** A shop card's cover. In one column (below 1000 px) a card is as wide as the page, so the picture also
 *  comes 1200 px wide; the browser picks by the width it will show. */
function ShopPhoto({ shot, alt }: { shot: (typeof HOME.shops.cards)[number]["shot"]; alt: string }) {
  const sizes = "(max-width: 1000px) calc(100vw - 108px), 330px";
  return (
    <picture>
      <source type="image/avif" srcSet={`${shot.avif} 300w, ${shot.avif2x} 600w, ${shot.avif4x} 1200w`} sizes={sizes} />
      <source type="image/webp" srcSet={`${shot.webp} 300w, ${shot.webp2x} 600w, ${shot.webp4x} 1200w`} sizes={sizes} />
      <img src={shot.webp2x} width={shot.width} height={shot.height} alt={alt} loading="lazy" decoding="async" style={{ width: "100%", height: "auto", display: "block" }} />
    </picture>
  );
}

export function Safe() {
  const s = HOME.safe;
  return (
    <section id={s.id} style={{ padding: "120px 0", scrollMarginTop: 20 }} aria-labelledby="safe-title">
      <div className="lx-wrap" style={{ display: "flex", flexDirection: "column", gap: 48 }}>
        <div style={{ display: "flex", flexDirection: "column", gap: 16, maxWidth: 760 }}>
          <h2 id="safe-title" style={H2}>{s.heading}</h2>
          <p style={SUB}>{s.sub}</p>
        </div>
        <div className="lx-safe">
          {s.items.map((item) => (
            <div key={item.title} style={{ borderRadius: 24, padding: 28, background: "#F5F6FA", display: "flex", flexDirection: "column", gap: 12 }}>
              <span style={{ display: "inline-flex", width: 48, height: 48, borderRadius: 14, background: item.bg, alignItems: "center", justifyContent: "center" }}>
                <svg width="24" height="24" viewBox="0 0 24 24" aria-hidden="true"><path d={item.icon} stroke="#14162B" strokeWidth="2" fill="none" strokeLinecap="round" strokeLinejoin="round" /></svg>
              </span>
              <h3 style={{ margin: 0, fontSize: 21, fontWeight: 850 }}>{item.title}</h3>
              <p style={{ margin: 0, fontSize: 16, lineHeight: 1.5, color: "#4B4F6B" }}>{item.text}</p>
            </div>
          ))}
        </div>
        <span style={{ display: "inline-flex", gap: 12, alignItems: "center", fontSize: 17, fontWeight: 650, color: "#3B3F5C" }}>
          <span style={{ display: "inline-flex", flexShrink: 0, width: 36, height: 36, borderRadius: 99, background: "#F1ECFF", alignItems: "center", justifyContent: "center" }}>
            <svg width="18" height="18" viewBox="0 0 24 24" aria-hidden="true"><path d="M4 5 H20 V16 H9 L5 20 V16 H4 Z" stroke="#14162B" strokeWidth="2" fill="none" strokeLinejoin="round" /></svg>
          </span>
          <span>
            <span>{s.contact.before}</span>
            <a href={s.contact.href} className="mk-link">{s.contact.link}</a>
            <span>{s.contact.after}</span>
          </span>
        </span>
      </div>
    </section>
  );
}

/** Avatar initials: white, ringed in ink. */
function Initials({ name, size }: { name: string; size: number }) {
  return (
    <span className="lx-cb" style={{ width: size, height: size, flexShrink: 0, borderRadius: 999, background: "#FFFFFF", border: `${size > 50 ? 3 : 2}px solid #14162B`, display: "inline-flex", alignItems: "center", justifyContent: "center", fontSize: size > 50 ? 20 : 15, fontWeight: 900 }} aria-hidden="true">{initials(name)}</span>
  );
}

/** The yellow section: the customers' own words, from config; our promise when there are none. */
export function Voices() {
  const { testimonials, promise, label } = HOME.voices;
  const [first, ...rest] = testimonials;
  return (
    <section style={{ background: "#FFD24A", padding: "110px 0" }} aria-label={first ? label : promise.label} data-testid="voices">
      <div className="lx-wrap" style={{ display: "flex", flexDirection: "column", gap: 26, maxWidth: 1000 }}>
        <svg width="56" height="42" viewBox="0 0 44 34" aria-hidden="true"><path d="M0 34 V20 Q0 4 16 0 V8 Q8 11 8 18 H16 V34 Z M26 34 V20 Q26 4 42 0 V8 Q34 11 34 18 H42 V34 Z" fill="#14162B" /></svg>
        {first ? (
          <figure key="quotes" style={{ margin: 0, display: "flex", flexDirection: "column", gap: 26 }} data-testid="testimonials">
            <blockquote style={{ margin: 0 }}>
              <p style={{ margin: 0, fontSize: "clamp(26px, 3.2vw, 40px)", lineHeight: 1.22, fontWeight: 850, fontStretch: "104%", letterSpacing: "-0.6px" }} data-testid="testimonial">{first.quote}</p>
            </blockquote>
            <figcaption style={{ display: "flex", gap: 16, alignItems: "center" }}>
              <Initials name={first.name} size={58} />
              <span style={{ display: "flex", flexDirection: "column", gap: 3 }}>
                <span style={{ fontSize: 18, fontWeight: 850 }}>{first.name}</span>
                <span style={{ fontSize: 15, color: "#3B3F5C" }}>{first.about}</span>
              </span>
            </figcaption>
            {rest.length ? (
              <div key="more" className="lx-two" style={{ marginTop: 22 }}>
                {rest.map((t) => (
                  <figure key={t.name} style={{ margin: 0, borderRadius: 24, padding: 28, background: "rgba(255,255,255,0.72)", display: "flex", flexDirection: "column", gap: 18 }}>
                    <blockquote style={{ margin: 0 }}>
                      <p style={{ margin: 0, fontSize: 19, lineHeight: 1.5, fontWeight: 650 }} data-testid="testimonial">{t.quote}</p>
                    </blockquote>
                    <figcaption style={{ display: "flex", gap: 12, alignItems: "center", marginTop: "auto" }}>
                      <Initials name={t.name} size={44} />
                      <span style={{ display: "flex", flexDirection: "column", gap: 2 }}>
                        <span style={{ fontSize: 16, fontWeight: 850 }}>{t.name}</span>
                        <span style={{ fontSize: 14, color: "#3B3F5C" }}>{t.about}</span>
                      </span>
                    </figcaption>
                  </figure>
                ))}
              </div>
            ) : null}
          </figure>
        ) : (
          <div key="promise" style={{ display: "flex", flexDirection: "column", gap: 22 }} data-testid="promise">
            <span style={{ fontSize: 18, fontWeight: 850 }}>{promise.label}</span>
            <p style={{ margin: 0, fontSize: "clamp(26px, 3.2vw, 40px)", lineHeight: 1.22, fontWeight: 850, fontStretch: "104%", letterSpacing: "-0.6px" }}>{promise.line}</p>
            <span style={{ display: "flex", flexWrap: "wrap", gap: 10 }}>
              {promise.badges.map((badge) => (
                <span key={badge} style={{ display: "inline-flex", gap: 8, alignItems: "center", fontSize: 15, fontWeight: 800, background: "rgba(255,255,255,0.72)", borderRadius: 999, padding: "8px 14px" }}>
                  <Check size={12} colour="#14162B" />
                  <span>{badge}</span>
                </span>
              ))}
            </span>
          </div>
        )}
      </div>
    </section>
  );
}

export function Pricing() {
  const p = HOME.pricing;
  return (
    <section id={p.id} style={{ padding: "120px 0", scrollMarginTop: 20 }} aria-labelledby="pricing-title">
      <div className="lx-wrap" style={{ display: "flex", flexDirection: "column", gap: 44 }}>
        <PlanCards>
          <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
            <h2 id="pricing-title" style={H2}>{p.heading}</h2>
            <p style={{ ...SUB, maxWidth: "30em" }}>{p.sub}</p>
          </div>
        </PlanCards>
        <span style={{ display: "flex", flexDirection: "column", gap: 6, fontSize: 15, color: "#6A6F8A" }}>
          <span>{p.note}</span>
          <span style={{ color: "#4B4F6B", fontWeight: 650 }} data-testid="planned-notice">{p.notice}</span>
        </span>
      </div>
    </section>
  );
}

export function Faq() {
  const f = HOME.faq;
  return (
    <section style={{ padding: "20px 0 120px" }} aria-labelledby="faq-title">
      <div className="lx-wrap" style={{ display: "flex", flexDirection: "column", gap: 48 }}>
        <h2 id="faq-title" style={{ margin: 0, fontSize: "clamp(32px, 3.6vw, 46px)", lineHeight: 1.05, fontWeight: 900, fontStretch: "110%", letterSpacing: "-1.2px" }}>{f.heading}</h2>
        <div className="lx-faq">
          {f.items.map((item) => (
            <div key={item.q} style={{ display: "flex", flexDirection: "column", gap: 10, borderTop: "2px solid #14162B", paddingTop: 20 }}>
              <h3 style={{ margin: 0, fontSize: 21, fontWeight: 850, lineHeight: 1.25 }}>{item.q}</h3>
              <p style={{ margin: 0, fontSize: 17, lineHeight: 1.55, color: "#4B4F6B" }}>{item.a}</p>
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}

export function Closing() {
  const c = HOME.closing;
  return (
    <section style={{ padding: "0 0 110px" }} aria-labelledby="closing-title">
      <div className="lx-wrap">
        <div className="lx-dark" style={{ borderRadius: 36, padding: "clamp(40px, 6vw, 80px)", background: "#14162B", color: "#FFFFFF", backgroundImage: "radial-gradient(600px 360px at 100% 0%, rgba(255,210,74,0.28), rgba(255,210,74,0) 70%), radial-gradient(500px 360px at 0% 100%, rgba(150,120,255,0.22), rgba(150,120,255,0) 70%)", display: "flex", flexWrap: "wrap", gap: 34, alignItems: "center", justifyContent: "space-between" }}>
          <div style={{ display: "flex", flexDirection: "column", gap: 16, maxWidth: "36em" }}>
            <h2 id="closing-title" style={{ margin: 0, fontSize: "clamp(36px, 4.6vw, 62px)", lineHeight: 0.98, fontWeight: 900, fontStretch: "112%", letterSpacing: "-1.8px", color: "#FFFFFF" }}>{c.heading}</h2>
            <span style={{ fontSize: 18, lineHeight: 1.5, color: "#C3C7DD" }}>{c.body}</span>
          </div>
          <a href={c.primary.href} className="lx-btn lx-btn-tag" style={{ fontSize: 20, fontWeight: 900, padding: "22px 36px", boxShadow: "0 14px 40px rgba(255,200,60,0.35)" }} data-testid="closing-cta">{c.primary.label}</a>
        </div>
      </div>
    </section>
  );
}
