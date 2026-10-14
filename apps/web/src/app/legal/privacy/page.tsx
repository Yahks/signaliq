export default function PrivacyPage() {
  return (
    <article className="mx-auto max-w-3xl px-4 py-12 text-sm leading-relaxed">
      <div className="mb-8 rounded-md border border-yellow-600/40 bg-yellow-500/10 p-4 text-xs">
        <strong>Draft — pending legal review.</strong> This text describes what
        data we process and why, but has not been reviewed by a lawyer. It is
        not yet the binding Privacy Policy.
      </div>

      <h1 className="mb-6 text-2xl font-semibold">Privacy Policy</h1>

      <section className="mb-6 space-y-2">
        <h2 className="text-base font-semibold">What we collect</h2>
        <ul className="list-disc space-y-1 pl-6">
          <li>Account details you provide at signup (email, name, country).</li>
          <li>
            Content you upload or generate within the service, and the
            metadata associated with it.
          </li>
          <li>
            API keys you configure for your chosen language-model providers,
            stored encrypted.
          </li>
          <li>
            Usage events needed to meter platform tokens and to operate the
            service.
          </li>
        </ul>
      </section>

      <section className="mb-6 space-y-2">
        <h2 className="text-base font-semibold">How we use it</h2>
        <ul className="list-disc space-y-1 pl-6">
          <li>To provide the service you asked for.</li>
          <li>To meter usage and enforce the token model.</li>
          <li>To verify content against its source data.</li>
          <li>To operate, secure, and improve the platform.</li>
        </ul>
      </section>

      <section className="mb-6 space-y-2">
        <h2 className="text-base font-semibold">Legal basis (GDPR)</h2>
        <p>
          Where GDPR applies, we process account and usage data to perform our
          contract with you, and we process operational and security data on
          the basis of legitimate interests.
        </p>
      </section>

      <section className="mb-6 space-y-2">
        <h2 className="text-base font-semibold">Third parties</h2>
        <p>
          Content generation depends on your configured language-model
          providers (for example Groq, Gemini, OpenRouter). Source data is
          fetched from public sources such as StockAnalysis, Yahoo Finance,
          and FRED. Each third party processes only what is needed to perform
          its part of the request.
        </p>
      </section>

      <section className="mb-6 space-y-2">
        <h2 className="text-base font-semibold">Data residency</h2>
        <p>
          Data is processed in the European Union and the United States. Where
          your organisation has a specific region configured, data is stored
          in that region.
        </p>
      </section>

      <section className="mb-6 space-y-2">
        <h2 className="text-base font-semibold">Retention</h2>
        <p>
          We retain account and usage data for as long as your account is
          active and for a reasonable period afterwards to satisfy legal and
          operational requirements.
        </p>
      </section>

      <section className="mb-6 space-y-2">
        <h2 className="text-base font-semibold">Your rights</h2>
        <p>
          Depending on your jurisdiction you may have rights to access,
          correct, export, or delete your data. Contact us to exercise them.
        </p>
      </section>

      <section className="mb-6 space-y-2">
        <h2 className="text-base font-semibold">Contact</h2>
        <p>[Contact address to be completed.]</p>
      </section>
    </article>
  );
}
