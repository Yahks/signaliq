export default function TermsPage() {
  return (
    <article className="mx-auto max-w-3xl px-4 py-12 text-sm leading-relaxed">
      <div className="mb-8 rounded-md border border-yellow-600/40 bg-yellow-500/10 p-4 text-xs">
        <strong>Draft — pending legal review.</strong> This text describes how
        the service operates but has not been reviewed by a lawyer. It is not
        yet the binding Terms of Service.
      </div>

      <h1 className="mb-6 text-2xl font-semibold">Terms of Service</h1>

      <section className="mb-6 space-y-2">
        <h2 className="text-base font-semibold">1. Acceptance</h2>
        <p>
          By creating an account on SignalIQ you agree to these Terms. If you
          do not agree, do not use the service.
        </p>
      </section>

      <section className="mb-6 space-y-2">
        <h2 className="text-base font-semibold">2. The service</h2>
        <p>
          SignalIQ produces statistical and editorial content across several
          service blocks. Output is generated from public data sources and is
          verified before publication, but no output is guaranteed to be
          accurate, complete, or fit for any particular purpose.
        </p>
      </section>

      <section className="mb-6 space-y-2">
        <h2 className="text-base font-semibold">3. Your account</h2>
        <p>
          You are responsible for maintaining the confidentiality of your
          credentials and for all activity under your account. You must be at
          least 18 years old.
        </p>
      </section>

      <section className="mb-6 space-y-2">
        <h2 className="text-base font-semibold">
          4. Your content and data
        </h2>
        <p>
          Content you upload remains yours. You grant us a license to process
          it in order to operate the service for your account.
        </p>
      </section>

      <section className="mb-6 space-y-2">
        <h2 className="text-base font-semibold">5. Our content</h2>
        <p>
          Content produced by SignalIQ for your account is made available to
          you. Statistical analysis is not advice — not investment advice, not
          betting advice, not legal, tax, or professional advice.
        </p>
      </section>

      <section className="mb-6 space-y-2">
        <h2 className="text-base font-semibold">6. Acceptable use</h2>
        <p>
          Do not use the service to violate any law, to infringe the rights of
          others, or to attempt to compromise the integrity of the platform.
        </p>
      </section>

      <section className="mb-6 space-y-2">
        <h2 className="text-base font-semibold">7. Fees and tokens</h2>
        <p>
          Usage is metered in platform tokens, granted at signup and consumed
          as content is produced. Token prices are listed on the{" "}
          <a href="/onboarding/guide" className="underline">
            onboarding guide
          </a>{" "}
          and may change.
        </p>
      </section>

      <section className="mb-6 space-y-2">
        <h2 className="text-base font-semibold">8. Termination</h2>
        <p>
          You may close your account at any time. We may suspend or terminate
          accounts that breach these Terms.
        </p>
      </section>

      <section className="mb-6 space-y-2">
        <h2 className="text-base font-semibold">9. Disclaimers</h2>
        <p>
          The service is provided on an &ldquo;as is&rdquo; and &ldquo;as
          available&rdquo; basis. To the maximum extent permitted by law, we
          disclaim all warranties not expressly stated here.
        </p>
      </section>

      <section className="mb-6 space-y-2">
        <h2 className="text-base font-semibold">
          10. Limitation of liability
        </h2>
        <p>
          To the maximum extent permitted by law, our total liability to you
          for any claim arising out of the service will not exceed the amount
          you paid us in the twelve months preceding the claim.
        </p>
      </section>

      <section className="mb-6 space-y-2">
        <h2 className="text-base font-semibold">11. Governing law</h2>
        <p>[To be completed by counsel.]</p>
      </section>

      <section className="mb-6 space-y-2">
        <h2 className="text-base font-semibold">12. Changes</h2>
        <p>
          We may update these Terms. Material changes will be communicated
          through the service.
        </p>
      </section>
    </article>
  );
}
