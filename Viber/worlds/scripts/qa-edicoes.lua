-- QA das edições vivas do terreno: uma vez por arranque, abre uma cratera e
-- levanta uma colina no campo (a engine re-mesha as colunas afetadas — mesh +
-- collider — pelo caminho staged do LOD).
local g = viber.game()

function on_update(dt)
  if g.qa_edicoes_feito then
    return
  end
  g.qa_edicoes_feito = true
  viber.terrain.crater(20, 0, 12, 5)
  viber.terrain.raise(-20, 0, 8, 4)
  viber.log("qa-edicoes: cratera em (20, 0) r12 d5 + colina em (-20, 0) r8 h4")
end
