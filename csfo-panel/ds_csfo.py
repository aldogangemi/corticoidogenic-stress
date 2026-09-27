"""
Dawid-Skene aggregation for CSFO-Polanyi multilayer pattern adjudication.

Setting: I candidate cross-layer patterns emitted by a CSFO-steered pipeline.
J=5 layer agents (genetic, hormonal, neural, cognitive, social), each on a
different backbone, each steered by its CSFO layer projection.

Each agent assigns one of K=3 verdicts to each pattern it is competent to judge:
  0 = SUPPORTED        (cross-layer relation is evidentially backed)
  1 = UNDERDETERMINED  (well-formed but not decidable from current evidence)
  2 = CONTRADICTED     (evidence or axioms speak against it)

There is NO gold standard for the latent verdict. Dawid-Skene treats the
verdict as a latent variable and estimates, jointly:
  - per-item posterior over verdicts        T[i,k]
  - per-agent confusion matrix              Pi[j][k,l] = P(agent j says l | truth k)
  - verdict prevalence                      p[k]

Ground truth is simulated here ONLY to score the estimator. In deployment
it is unavailable; what remains available is (a) the posterior with its
uncertainty and (b) the estimated confusion matrices.
"""

import numpy as np

RNG = np.random.default_rng(7)
K = 3
LAYERS = ["genetic", "hormonal", "neural", "cognitive", "social"]
VERDICTS = ["SUPPORTED", "UNDERDET", "CONTRAD"]


# ---------------------------------------------------------------- generative model
def make_confusions(correlated=False):
    """True (unknown to the estimator) per-agent confusion matrices.

    Deliberately heterogeneous: different failure modes, not just different
    accuracy. Rows = true verdict, cols = emitted verdict.
    """
    Pi = {
        # strong on refutation, conflates supported/underdetermined
        "genetic": [[0.55, 0.40, 0.05], [0.25, 0.70, 0.05], [0.03, 0.10, 0.87]],
        # well calibrated overall
        "hormonal": [[0.82, 0.13, 0.05], [0.12, 0.80, 0.08], [0.05, 0.12, 0.83]],
        # noisy, mild optimism
        "neural": [[0.70, 0.22, 0.08], [0.30, 0.55, 0.15], [0.15, 0.25, 0.60]],
        # conservative: over-calls UNDERDETERMINED
        "cognitive": [[0.50, 0.45, 0.05], [0.08, 0.88, 0.04], [0.06, 0.40, 0.54]],
        # lenient: rarely contradicts anything
        "social": [[0.85, 0.13, 0.02], [0.45, 0.50, 0.05], [0.35, 0.35, 0.30]],
    }
    if correlated:
        # three agents share a backbone family -> shared literature-inherited bias:
        # they jointly over-call SUPPORTED on a subset of items (applied later).
        pass
    return {k: np.array(v) for k, v in Pi.items()}


def simulate(I=400, prevalence=(0.30, 0.50, 0.20), coverage=0.75,
             correlated_bias=False, bias_frac=0.20, bias_group=("neural", "cognitive", "social")):
    """Emit a label matrix L[i,j] in {0,1,2} or -1 for abstention."""
    Pi = make_confusions()
    truth = RNG.choice(K, size=I, p=prevalence)
    L = -np.ones((I, len(LAYERS)), dtype=int)

    # items where a correlated group shares an inherited error
    biased = np.zeros(I, dtype=bool)
    if correlated_bias:
        biased[RNG.choice(I, size=int(bias_frac * I), replace=False)] = True

    for j, name in enumerate(LAYERS):
        # each agent judges only the patterns touching its layer (missing at random)
        judges = RNG.random(I) < coverage
        for i in np.where(judges)[0]:
            if correlated_bias and biased[i] and name in bias_group:
                L[i, j] = 0  # shared confabulation: "SUPPORTED"
            else:
                L[i, j] = RNG.choice(K, p=Pi[name][truth[i]])
    return L, truth, Pi, biased


# ---------------------------------------------------------------- Dawid-Skene EM
def dawid_skene(L, K=3, anchors=None, iters=300, tol=1e-10, prior=1.0):
    """anchors: dict {item_index: true_class} -- a small set of layer-internal
    items whose verdict IS known (replicated GWAS loci, assay-validity facts,
    established cognitive effects). They pin the label identities and sharpen
    the confusion estimates; they are NOT a gold standard for the target items."""
    I, J = L.shape
    obs = L >= 0

    # init: soft majority vote
    T = np.full((I, K), 1e-9)
    for i in range(I):
        for j in range(J):
            if obs[i, j]:
                T[i, L[i, j]] += 1
    T /= T.sum(1, keepdims=True)
    if anchors:
        for i, k in anchors.items():
            T[i] = 0.0
            T[i, k] = 1.0

    prev = None
    for _ in range(iters):
        # M-step
        p = T.sum(0) + prior
        p /= p.sum()
        Pi = np.zeros((J, K, K))
        for j in range(J):
            m = obs[:, j]
            for l in range(K):
                Pi[j, :, l] = T[m & (L[:, j] == l)].sum(0)
            Pi[j] += prior
            Pi[j] /= Pi[j].sum(1, keepdims=True)

        # E-step
        logT = np.log(p)[None, :].repeat(I, 0)
        for j in range(J):
            m = obs[:, j]
            logT[m] += np.log(Pi[j][:, L[m, j]]).T
        logT -= logT.max(1, keepdims=True)
        T = np.exp(logT)
        T /= T.sum(1, keepdims=True)
        if anchors:
            for i, k in anchors.items():
                T[i] = 0.0
                T[i, k] = 1.0

        ll = float(np.sum(logT))
        if prev is not None and abs(ll - prev) < tol:
            break
        prev = ll
    return T, Pi, p


def majority_vote(L, K=3):
    I, J = L.shape
    out = np.zeros(I, dtype=int)
    for i in range(I):
        v = L[i][L[i] >= 0]
        counts = np.bincount(v, minlength=K)
        out[i] = int(np.argmax(counts))  # ties -> lowest index
    return out


# ---------------------------------------------------------------- experiments
def report(tag, L, truth, Pi_true, anchors=None, biased=None):
    T, Pi_hat, p_hat = dawid_skene(L, anchors=anchors)
    ds = T.argmax(1)
    mv = majority_vote(L)
    conf = T.max(1)

    print(f"\n=== {tag} ===")
    print(f"majority-vote accuracy : {(mv == truth).mean():.3f}")
    print(f"Dawid-Skene accuracy   : {(ds == truth).mean():.3f}")
    print(f"prevalence  true {np.bincount(truth, minlength=K)/len(truth)}"
          f"  est {np.round(p_hat,3)}")

    print("\n per-agent diagonal (true -> estimated), i.e. recovered competence")
    for j, name in enumerate(LAYERS):
        dt = np.diag(Pi_true[name]).round(2)
        dh = np.diag(Pi_hat[j]).round(2)
        print(f"   {name:<10} true {dt}   est {dh}")

    # selective prediction: what DS buys you when you can defer
    for thr in (0.90, 0.99):
        keep = conf >= thr
        if keep.sum():
            print(f" DS acc on items with posterior >= {thr}: "
                  f"{(ds[keep]==truth[keep]).mean():.3f}  (kept {keep.mean():.0%})")

    if biased is not None and biased.any():
        print(f" accuracy on correlated-error items : "
              f"{(ds[biased]==truth[biased]).mean():.3f}"
              f"   mean posterior there: {conf[biased].mean():.3f}")
        print(f" accuracy elsewhere                 : "
              f"{(ds[~biased]==truth[~biased]).mean():.3f}"
              f"   mean posterior there: {conf[~biased].mean():.3f}")
    return T, Pi_hat


if __name__ == "__main__":
    # (1) conditional independence holds: the assumption DS needs
    L, truth, Pi_true, biased = simulate()
    report("A. no anchors, independent agent errors", L, truth, Pi_true)

    # (2) same data + 25 anchor items with known layer-internal verdicts
    idx = RNG.choice(len(truth), size=25, replace=False)
    anchors = {int(i): int(truth[i]) for i in idx}
    report("B. + 25 anchor items", L, truth, Pi_true, anchors=anchors)

    # (3) three agents share an inherited bias on 20% of items
    L2, truth2, Pi_true2, biased2 = simulate(correlated_bias=True)
    idx2 = RNG.choice(len(truth2), size=25, replace=False)
    anchors2 = {int(i): int(truth2[i]) for i in idx2}
    report("C. correlated errors in 3/5 agents (DS assumption violated)",
           L2, truth2, Pi_true2, anchors=anchors2, biased=biased2)
