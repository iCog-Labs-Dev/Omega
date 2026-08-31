(= (initPlugins)
   (progn
       (loadPlugins (py-call (plugin.listPlugins)))
       ()))

; Plugin loader

(= (loadPlugins $list)
   (map-atom $list $plugin (loadPlugin $plugin)))

(= (loadPlugin (python $name $location))
   (py-call (plugin.loadPythonPlugin $name $location)))

(= (loadPlugin (metta $name $location))
   (let True (atom-string $location $locationstr)
       (loadMettaPlugin $name $locationstr)))

(= (loadMettaPlugin $name $location)
   (let $rc1
       (if (== $location "")
         (Error (_loadMettaPlugin $name $location) (strings-concat ("Location is empty")))
         (progn
           (let $path (joinPath ($location $name)) ())
           (let $space (atom-concat & (atom-concat plugin- $name)) ())
           (log INFO "plugin" (strings-concat ("Loading MeTTa plugin " $name " from " $path ".metta")))
           ; put the plugin folder on sys.path so it can py-call into its own .py files
           (let $addpathrc (py-call (plugin.addLocationToPath $location)) ())
             (if (== () (collapse (import! $space $path)))
               (Error (_loadMettaPlugin $name $location) (strings-concat ("Cannot import " $name " plugin from " $path)))
               (progn
                 ; the pre-rename entry point is still evaluated so older plugins keep loading
                 (let $rcs (collapse (eval (loadOmegaPlugin))) ())
                 (let $rcsLegacy (collapse (eval (loadOmegaClawPlugin))) ())
                 ; collapse keeps a non-matching name from collapsing the whole branch
                 (let $rm (collapse (match $space (= (loadOmegaPlugin) $body) (remove-atom $space (= (loadOmegaPlugin) $body)))) ())
                 (let $rmLegacy (collapse (match $space (= (loadOmegaClawPlugin) $body) (remove-atom $space (= (loadOmegaClawPlugin) $body)))) ())
                 (let $agg (|-> ($acc $next) (if (== $acc ()) (if (== Error (car-atom $next)) $next ()) ()))
                   (let $rcNew (foldl-atom $rcs () $agg)
                     (let $rc (if (== $rcNew ()) (foldl-atom $rcsLegacy () $agg) $rcNew) ())))
                 $rc))))
       (progn
         (if (= $rc1 (cons Error (cons $_ $error)))
             (progn
               (log ERROR "plugin" $error)
               (log ERROR "plugin" (strings-concat ("Plugin " $name " loading failed")))
               $rc1)
             (progn
               (log INFO "plugin" (strings-concat ("Plugin " $name " is loaded")))
               ())))))
