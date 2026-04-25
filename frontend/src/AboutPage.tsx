import logoUrl from "./assets/circle-jerks-logo.png";

export default function AboutPage() {
  return (
    <main className="about-shell">
      <article className="about-page">
        <a className="about-logo-link" href="/" aria-label="Return to Circle Jerks">
          <img src={logoUrl} alt="Circle Jerks" />
        </a>
        <div className="eyebrow">Why I Wrote This</div>
        <h1>Small planes, big consequences.</h1>
        <div className="about-copy">
          <p>
            I used to be drawn to small-engine aircraft. I still love the freedom of flying and the idea of
            being able to lift off and go somewhere under your own control. In college, my roommate took me
            up in a small plane. I was broke, but I was hooked on the thought that someday I might earn my
            own private pilot&apos;s license.
          </p>
          <p>
            Fast-forward 20 years. I settled in a mid-sized town outside Denver, Colorado, where I would
            occasionally look up and smile at a plane passing overhead, wondering where it was headed. When
            I first moved here, planes came by maybe once an hour, usually well above 1,000 feet.
          </p>
          <p>
            Over the 16 years I have lived here, that has changed. Landing fees and airport policies around
            the Denver area have pushed more flight training into the few communities where those costs are
            lower. My town has become one of those places. Students and private pilots now come here to
            practice traffic patterns, circling all day, every day.
          </p>
          <p>
            It has reached the point where I cannot reliably carry on a conversation outdoors. Phone calls,
            Zoom meetings, and simple time outside are interrupted by constant aircraft noise overhead. There
            is also the issue of leaded aviation fuel, which means these flights are not just noisy; they are
            spreading lead pollution over the neighborhoods below.
          </p>
          <p>
            I have filed complaints, called the local airport, contacted the FAA, attended city council
            meetings, and tried to get this addressed for the 99.99% of the community that does not get to
            fly small aircraft but has to live under the noise. There is strength in numbers. My hope is that
            this tool helps people document what is happening, speak up together, and push for real change,
            not only here, but in every community whose homes have become a playground for the few in the sky.
          </p>
        </div>
      </article>
    </main>
  );
}
