; The plugin loader adds this directory to Python's module search path.
(= (loadOmegaPlugin)
   (progn
     (add-skill render-diagram
       "Render an exact diagram from Graphviz DOT source, preserving the supplied labels and edges. Use for flowcharts, dependency graphs and architecture diagrams instead of generative image tools. Pass complete DOT source, not a natural-language description or Markdown fences. Does not send a photo. Temporary artifacts are removed by default. Returns local PNG and DOT paths only when the host enables retainDiagramArtifacts; retained files expire and may be pruned."
       (dot_source))
     ()))

(= (render-diagram $source)
   (py-call (render_diagram.render_diagram $source (memoryDirectory)
     (py-call (config.config_get_by_key "retainDiagramArtifacts" False)))))
