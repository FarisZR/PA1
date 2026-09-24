// Styling for the review PDF. scripts/review/diff_ast.py emits calls to these
// functions; the normal paper never includes this file.

// Changes made anywhere in the pull request.
#let rv-add = rgb("#1a7f37")
#let rv-add-bg = rgb("#d5f5dc")
#let rv-rem = rgb("#cf222e")
#let rv-rem-bg = rgb("#ffe4e1")
// Changes made by the latest commit.
#let rv-new-add = rgb("#0969da")
#let rv-new-add-bg = rgb("#d6ecff")
#let rv-new-rem = rgb("#8250df")
#let rv-new-rem-bg = rgb("#f1e3ff")
// Block markers.
#let rv-edit = rgb("#bf8700")
#let rv-frame = rgb("#d1242f")

#let rv-ins(latest: false, body) = {
  let fg = if latest { rv-new-add } else { rv-add }
  let bg = if latest { rv-new-add-bg } else { rv-add-bg }
  highlight(fill: bg, extent: 0.6pt, underline(stroke: 0.8pt + fg, offset: 1.8pt, evade: false, body))
}

#let rv-del(latest: false, body) = {
  let fg = if latest { rv-new-rem } else { rv-rem }
  let bg = if latest { rv-new-rem-bg } else { rv-rem-bg }
  highlight(fill: bg, extent: 0.6pt, text(fill: fg, strike(stroke: 0.9pt + fg, body)))
}

#let rv-block(kind: "mod", latest: false, nested: false, body) = {
  let (colour, fill, label) = if kind == "ins" {
    if latest { (rv-new-add, rv-new-add-bg, "added") } else { (rv-add, rv-add-bg, "added") }
  } else if kind == "del" {
    if latest { (rv-new-rem, rv-new-rem-bg, "removed") } else { (rv-rem, rv-rem-bg, "removed") }
  } else if kind == "fig-changed" {
    (if latest { rv-new-add } else { rv-frame }, none, "figure changed")
  } else if kind == "fig-added" {
    (if latest { rv-new-add } else { rv-frame }, none, "new figure")
  } else {
    (if latest { rv-new-add } else { rv-edit }, none, "edited")
  }

  if nested {
    return block(width: 100%, fill: fill, outset: (x: 3pt, y: 3pt), radius: 1pt, body)
  }

  // Label in the left margin, next to the start of the change.
  let tag = place(top + left, dx: -2.5cm + 6pt, box(width: 2.5cm - 20pt, align(right, text(
    size: 6.5pt,
    weight: "bold",
    fill: colour,
    hyphenate: false,
    if latest [#label \ #text(weight: "regular")[latest commit]] else [#label],
  ))))

  if kind.starts-with("fig") {
    block(width: 100%, stroke: 2pt + colour, inset: 6pt, radius: 3pt, breakable: false, {
      tag
      body
    })
  } else {
    block(
      width: 100%,
      fill: fill,
      stroke: (left: 3pt + colour),
      outset: (left: 9pt, right: if fill == none { 0pt } else { 3pt }, y: 3pt),
      breakable: true,
      {
        tag
        body
      },
    )
  }
}

#let rv-legend = {
  set text(size: 7pt)
  [#rv-ins[added] #rv-del[removed] in this PR · ]
  [#rv-ins(latest: true)[added] #rv-del(latest: true)[removed] in the latest commit · ]
  [#box(width: 2.5pt, height: 7pt, fill: rv-edit) edited block · ]
  [#box(width: 9pt, height: 7pt, stroke: 1.2pt + rv-frame, radius: 1pt) changed figure]
}

#set page(header: context {
  set text(size: 7pt, fill: luma(90))
  grid(
    columns: (1fr, auto),
    align: (left + horizon, right + horizon),
    [*Review copy* · #rv-legend],
    [p. #counter(page).display("1")],
  )
})

#let rv-describe(kind) = (
  mod: "text edited",
  ins: "text added",
  del: "text removed",
  fig-changed: "figure changed",
  fig-added: "figure added",
).at(kind)

#let rv-summary(base: "", prev: "", head: "", entries) = {
  heading(outlined: false, numbering: none)[Changes in this pull request]
  [Base #raw(base) → head #raw(head).]
  if prev != "" [ The latest commit's own changes (#raw(prev) → #raw(head)) use the second colour pair.]
  parbreak()
  block(inset: 6pt, stroke: 0.5pt + luma(200), radius: 2pt, rv-legend)
  if entries.len() == 0 {
    [No visible change to the rendered paper.]
    return
  }
  set text(size: 9pt)
  table(
    columns: (auto, 1fr, auto, auto),
    stroke: none,
    inset: (x: 4pt, y: 3pt),
    table.header([*Page*], [*Section*], [*Change*], [*Latest commit*]),
    table.hline(stroke: 0.5pt),
    ..entries
      .map(e => (
        context link(e.id, str(counter(page).at(locate(e.id)).first())),
        context {
          let found = query(selector(heading.where(outlined: true)).before(locate(e.id)))
          if found.len() > 0 {
            let h = found.last()
            if h.numbering != none { numbering(h.numbering, ..counter(heading).at(h.location())) + " " }
            h.body
          }
        },
        rv-describe(e.kind),
        if e.latest { text(fill: rv-new-add, weight: "bold")[yes] } else [],
      ))
      .flatten(),
  )
}
