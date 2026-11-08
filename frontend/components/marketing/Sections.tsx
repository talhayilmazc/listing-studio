import { Check, Photo, Spark } from "@/components/marketing/HeroDemo";
import { PlanCards } from "@/components/marketing/PlanCards";
import { HOME } from "@/lib/homeCopy";
import { initials } from "@/lib/marketing/testimonials";

/**
 * The home page below the hero, section by section as the reference draws them
 * (docs/design/home-reference.dc.html): the proof strip, the grind, four steps, every shop's own version,
 * safety, what sellers say, pricing, questions and the closing call. The reference's values are the lx-
 * classes in app/globals.css (written once, not on every element); colours that differ per item stay on the
 * element. Every word comes from lib/homeCopy.ts; prices and the cost of a listing from lib/plans.ts, the
 * customers' words from lib/marketing/testimonials.ts.
 */

/** A step's number and title: the number in muted text (4.9:1; the reference's faint grey is 2.6:1). */
function StepTitle({ n, title }: { n: string; title: string }) {
  return (
    <div className="lx-step-title">
      <span className="lx-step-n">{n}</span>
      <h3 className="lx-h3">{title}</h3>
    </div>
  );
}

export function Strip() {
  return (
    <section className="lx-strip" aria-label="What you can count on">
      <div className="lx-wrap lx-strip-in">
        {HOME.strip.map((item) => (
          <span key={item}>
            <span className="lx-strip-box">
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
    <section className="lx-sec-grind" aria-labelledby="grind-title">
      <div className="lx-wrap lx-stack-52">
        <div className="lx-head-16">
          <h2 id="grind-title" className="lx-h2">{g.heading}</h2>
          <p className="lx-lead">{g.sub}</p>
        </div>
        <div className="lx-two">
          <div className="lx-byhand">
            <span className="lx-byhand-label">{g.byHandLabel}</span>
            {g.byHand.map((item) => (
              <span key={item} className="lx-byhand-item">
                <svg width="22" height="22" viewBox="0 0 22 22" aria-hidden="true"><circle cx="11" cy="11" r="9" fill="none" stroke="#B9BDCE" strokeWidth="2" /><path d="M11 6 V11 L14.5 13" stroke="#B9BDCE" strokeWidth="2" fill="none" strokeLinecap="round" /></svg>
                <span className="lx-struck">{item}</span>
              </span>
            ))}
          </div>
          <div className="lx-withus lx-dark">
            <span className="lx-withus-label">{g.withLabel}</span>
            {g.withUs.map((item) => (
              <span key={item} className="lx-withus-item">
                <span className="lx-tick">
                  <svg width="16" height="16" viewBox="0 0 16 16" aria-hidden="true"><path d="M3.2 8.4 L6.6 11.4 L12.8 4.8" stroke="#14162B" strokeWidth="2.6" fill="none" strokeLinecap="round" strokeLinejoin="round" /></svg>
                </span>
                <span>{item}</span>
              </span>
            ))}
            <span className="lx-withus-note">{g.note}</span>
          </div>
        </div>
      </div>
    </section>
  );
}

export function How() {
  const h = HOME.how;
  const [s1, s2, s3, s4] = h.steps;
  return (
    <section id={h.id} className="lx-sec-how" aria-labelledby="how-title">
      <div className="lx-wrap lx-stack-48">
        <h2 id="how-title" className="lx-h2" style={{ maxWidth: "14em" }}>{h.heading}</h2>
        <div className="lx-steps">
          <div className="lx-step">
            <div className="lx-step-art lx-art-files">
              {h.files.map((f) => (
                <span key={f.n} className="lx-file">
                  <span className="lx-file-dot" style={{ background: f.c }} />
                  <span>{f.n}</span>
                </span>
              ))}
            </div>
            <StepTitle n={s1.n} title={s1.title} />
            <p className="lx-step-text">{s1.text}</p>
          </div>

          <div className="lx-step">
            <div className="lx-step-art lx-art-colours" data-testid="step-colours">
              {h.colours.map((c) => (
                <div key={c.alt} className="lx-colour">
                  <Photo shot={c.shot} alt={c.alt} />
                </div>
              ))}
            </div>
            <StepTitle n={s2.n} title={s2.title} />
            <p className="lx-step-text">{s2.text}</p>
          </div>

          <div className="lx-step">
            <div className="lx-step-art lx-art-written">
              {/* go-ink, 5.3:1 on its tint; the reference's go green there is 3.9:1 */}
              <span className="lx-written">
                <Spark size={14} colour="#1F8A4C" />
                <span>{h.written}</span>
              </span>
              <span className="lx-written-title">{h.writtenTitle}</span>
              <div className="lx-chips-5">
                {h.tagsShort.map((tag) => (
                  <span key={tag} className="lx-chip-white">{tag}</span>
                ))}
              </div>
            </div>
            <StepTitle n={s3.n} title={s3.title} />
            <p className="lx-step-text">{s3.text}</p>
          </div>

          <div className="lx-step">
            <div className="lx-step-art lx-art-days">
              <div className="lx-days">
                {h.days.map((d, i) => (
                  <span key={i} className="lx-day">
                    <span className="lx-day-n">{d.n}</span>
                    <span className="lx-day-bar" style={{ background: d.a }} />
                    <span className="lx-day-bar" style={{ background: d.b }} />
                    <span className="lx-day-bar" style={{ background: d.c }} />
                  </span>
                ))}
              </div>
              <span className="lx-legend">
                {h.legend.map((l) => (
                  <span key={l.name}>
                    <span className="lx-dot8" style={{ background: l.dot }} />
                    <span>{l.name}</span>
                  </span>
                ))}
              </span>
            </div>
            <StepTitle n={s4.n} title={s4.title} />
            <p className="lx-step-text">{s4.text}</p>
          </div>
        </div>
      </div>
    </section>
  );
}

export function ShopVersions() {
  const s = HOME.shops;
  return (
    <section className="lx-sec-dark lx-dark" aria-labelledby="shops-title">
      <div className="lx-wrap lx-stack-52">
        <div className="lx-split">
          <h2 id="shops-title" className="lx-h2" style={{ maxWidth: "12em", color: "#FFFFFF" }}>{s.heading}</h2>
          <p className="lx-shops-sub">{s.sub}</p>
        </div>
        <div className="lx-shops" data-testid="shop-cards">
          {s.cards.map((c) => (
            <div key={c.shop} className="lx-shop-card">
              <span className="lx-shop-card-head">
                <span className="lx-shop-card-name">
                  <span className="lx-dot10" style={{ background: c.dot }} />
                  <span>{c.shop}</span>
                </span>
                <span className="lx-faint">{c.when}</span>
              </span>
              <div className="lx-cover">
                <ShopPhoto shot={c.shot} alt={c.alt} />
              </div>
              <span className="lx-card-title">{c.title}</span>
              <span className="lx-pills">
                <span className="lx-pill-dark" translate="no">{`SKU ${c.sku}`}</span>
                <span className="lx-pill-dark">{c.extra}</span>
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
      <img src={shot.webp2x} width={shot.width} height={shot.height} alt={alt} loading="lazy" decoding="async" className="lx-img" />
    </picture>
  );
}

export function Safe() {
  const s = HOME.safe;
  return (
    <section id={s.id} className="lx-sec-120" aria-labelledby="safe-title">
      <div className="lx-wrap lx-stack-48">
        <div className="lx-head-16">
          <h2 id="safe-title" className="lx-h2">{s.heading}</h2>
          <p className="lx-lead">{s.sub}</p>
        </div>
        <div className="lx-safe">
          {s.items.map((item) => (
            <div key={item.title} className="lx-safe-card">
              <span className="lx-safe-icon" style={{ background: item.bg }}>
                <svg width="24" height="24" viewBox="0 0 24 24" aria-hidden="true"><path d={item.icon} stroke="#14162B" strokeWidth="2" fill="none" strokeLinecap="round" strokeLinejoin="round" /></svg>
              </span>
              <h3 className="lx-h3-21">{item.title}</h3>
              <p className="lx-card-text">{item.text}</p>
            </div>
          ))}
        </div>
        <span className="lx-person">
          <span className="lx-person-dot">
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
function Initials({ name, size }: { name: string; size: 58 | 44 }) {
  return <span className={"lx-avatar " + (size === 58 ? "lx-avatar-lg" : "lx-avatar-sm")} aria-hidden="true">{initials(name)}</span>;
}

/** The yellow section: the customers' own words, from config; our promise when there are none. */
export function Voices() {
  const { testimonials, promise, label } = HOME.voices;
  const [first, ...rest] = testimonials;
  return (
    <section className="lx-sec-yellow" aria-label={first ? label : promise.label} data-testid="voices">
      <div className="lx-wrap lx-voices">
        <svg width="56" height="42" viewBox="0 0 44 34" aria-hidden="true"><path d="M0 34 V20 Q0 4 16 0 V8 Q8 11 8 18 H16 V34 Z M26 34 V20 Q26 4 42 0 V8 Q34 11 34 18 H42 V34 Z" fill="#14162B" /></svg>
        {first ? (
          <figure key="quotes" className="lx-figure" data-testid="testimonials">
            <blockquote className="lx-blockquote">
              <p className="lx-quote-big" data-testid="testimonial">{first.quote}</p>
            </blockquote>
            <figcaption className="lx-who">
              <Initials name={first.name} size={58} />
              <span className="lx-who-lines">
                <span className="lx-who-name-lg">{first.name}</span>
                <span className="lx-who-about-lg">{first.about}</span>
              </span>
            </figcaption>
            {rest.length ? (
              <div key="more" className="lx-two lx-more">
                {rest.map((t) => (
                  <figure key={t.name} className="lx-quote-card">
                    <blockquote className="lx-blockquote">
                      <p className="lx-quote" data-testid="testimonial">{t.quote}</p>
                    </blockquote>
                    <figcaption className="lx-who-sm">
                      <Initials name={t.name} size={44} />
                      <span className="lx-who-lines">
                        <span className="lx-who-name">{t.name}</span>
                        <span className="lx-who-about">{t.about}</span>
                      </span>
                    </figcaption>
                  </figure>
                ))}
              </div>
            ) : null}
          </figure>
        ) : (
          <div key="promise" className="lx-promise" data-testid="promise">
            <span className="lx-promise-label">{promise.label}</span>
            <p className="lx-quote-big">{promise.line}</p>
            <span className="lx-promise-badges">
              {promise.badges.map((badge) => (
                <span key={badge} className="lx-promise-badge">
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
    <section id={p.id} className="lx-sec-120" aria-labelledby="pricing-title">
      <div className="lx-wrap lx-stack-44">
        <PlanCards>
          <div className="lx-head-14">
            <h2 id="pricing-title" className="lx-h2">{p.heading}</h2>
            <p className="lx-lead lx-price-sub">{p.sub}</p>
          </div>
        </PlanCards>
        <span className="lx-price-note">
          <span>{p.note}</span>
          <span className="lx-price-notice" data-testid="planned-notice">{p.notice}</span>
        </span>
      </div>
    </section>
  );
}

export function Faq() {
  const f = HOME.faq;
  return (
    <section className="lx-sec-faq" aria-labelledby="faq-title">
      <div className="lx-wrap lx-stack-48">
        <h2 id="faq-title" className="lx-h2-faq">{f.heading}</h2>
        <div className="lx-faq">
          {f.items.map((item) => (
            <div key={item.q} className="lx-faq-item">
              <h3 className="lx-faq-q">{item.q}</h3>
              <p className="lx-faq-a">{item.a}</p>
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
    <section className="lx-sec-closing" aria-labelledby="closing-title">
      <div className="lx-wrap">
        <div className="lx-closing lx-dark">
          <div className="lx-closing-text">
            <h2 id="closing-title" className="lx-closing-h">{c.heading}</h2>
            <span className="lx-closing-body">{c.body}</span>
          </div>
          <a href={c.primary.href} className="lx-btn lx-btn-tag lx-closing-cta" data-testid="closing-cta">{c.primary.label}</a>
        </div>
      </div>
    </section>
  );
}
