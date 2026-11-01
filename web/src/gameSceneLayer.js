import * as THREE from 'three'
import { readBoardLayout, baseToGameScene } from './gameBoardLayout.js'

/* 3D 棋类显示层：只渲染后端 2D 局面，不驱动机器人，也不产生任何运动指令。
   棋子用真实模型（国际象棋为 CC-BY-4.0 的 Khronos「A Beautiful Game」裁剪件），
   围棋/五子棋用凸透镜棋子，中国象棋用带刻字的圆棋子。
   2D 棋盘负责操作，这一层负责“看得见的同一局棋”。 */

const CHESS_MODEL_URL = '/models/games/chess_pieces.glb'
const CHESS_NODE = {
  K: { name: color => `King_${color}`, height: 0.052 },
  Q: { name: color => `Queen_${color}`, height: 0.048 },
  R: { name: color => `Castle_${color}1`, height: 0.044 },
  B: { name: color => `Bishop_${color}1`, height: 0.046 },
  N: { name: color => `Knight_${color}1`, height: 0.046 },
  P: { name: color => `Pawn_Body_${color}1`, height: 0.034 },
}
const CHESS_ORDER = { K: 'king', Q: 'queen', R: 'rook', B: 'bishop', N: 'knight', P: 'pawn' }

const ease = t => t < .5 ? 2 * t * t : 1 - ((-2 * t + 2) ** 2) / 2

export function createGameLayer({ parent, position = [0, 0, 0] }) {
  const root = new THREE.Group()
  root.position.set(...position)
  root.visible = false
  root.name = 'game-layer'
  parent.add(root)

  const boardGroup = new THREE.Group()
  const pieceGroup = new THREE.Group()
  const markerGroup = new THREE.Group()
  root.add(boardGroup, pieceGroup, markerGroup)

  let sceneObjects = [], physicalLayout = null, disposed = false
  const trayGroup = new THREE.Group()
  root.add(trayGroup)
  let state = null
  let boardKey = ''
  let boardMesh = null
  let standMesh = null
  let time = 0
  let templates = null
  let templateJob = null
  const pieces = []
  const removals = []
  const carried = new Map()
  let metricsNow = null
  let physical = []
  let suggestMarker = null
  let selectedMarker = null

  // 共享几何与材质：每局重建棋盘，棋子实例只共享，不逐个 dispose。
  const ringGeo = new THREE.RingGeometry(.82, 1, 40)
  ringGeo.rotateX(-Math.PI / 2)
  const dotGeo = new THREE.CircleGeometry(.34, 24)
  dotGeo.rotateX(-Math.PI / 2)
  const textGeo = new THREE.PlaneGeometry(.62, .62)
  textGeo.rotateX(-Math.PI / 2)

  const mats = {
    white: new THREE.MeshStandardMaterial({ color: 0xf1e7d4, roughness: .38, metalness: .08 }),
    black: new THREE.MeshStandardMaterial({ color: 0x20252c, roughness: .34, metalness: .12 }),
    stoneBlack: new THREE.MeshStandardMaterial({ color: 0x171b1f, roughness: .22, metalness: .05 }),
    stoneWhite: new THREE.MeshStandardMaterial({ color: 0xe9e4d8, roughness: .25, metalness: .03 }),
    disc: new THREE.MeshStandardMaterial({ color: 0xdcc9a1, roughness: .62, metalness: .02 }),
    accent: new THREE.MeshBasicMaterial({ color: 0x2fd6f0, transparent: true, opacity: .75, depthWrite: false }),
    suggest: new THREE.MeshBasicMaterial({ color: 0x2fd6f0, transparent: true, opacity: .9, depthWrite: false }),
    danger: new THREE.MeshBasicMaterial({ color: 0xe4553f, transparent: true, opacity: .8, depthWrite: false }),
    last: new THREE.MeshBasicMaterial({ color: 0xe0a33b, transparent: true, opacity: .85, depthWrite: false }),
    lastFrom: new THREE.MeshBasicMaterial({ color: 0xe0a33b, transparent: true, opacity: .42, depthWrite: false }),
    check: new THREE.MeshBasicMaterial({ color: 0xe4553f, transparent: true, opacity: .9, depthWrite: false }),
    select: new THREE.MeshBasicMaterial({ color: 0x2fd6f0, transparent: true, opacity: .9, depthWrite: false }),
  }
  const textMaterials = new Map()
  const tttRingGeo=new THREE.TorusGeometry(.032,.009,12,40)
  const tttBarGeo=new THREE.BoxGeometry(.100,.018,.016)

  function cellMetrics(current) {
    const rows = Number(current.rows) || 1
    const cols = Number(current.cols) || 1
    const span = physicalLayout.span_mm / 1000
    const cell = span / Math.max(rows, cols)
    const points = current.layout === 'points'
    if (points) {
      return { rows, cols, cell, points, width: (cols - 1) * cell + cell * 1.2,
               depth: (rows - 1) * cell + cell * 1.2 }
    }
    return { rows, cols, cell, points, width: cols * cell, depth: rows * cell }
  }

  function pointFor(metrics, r, c) {
    if (metrics.points) return new THREE.Vector3((c - (metrics.cols - 1) / 2) * metrics.cell, 0,
      (r - (metrics.rows - 1) / 2) * metrics.cell)
    return new THREE.Vector3((c + .5 - metrics.cols / 2) * metrics.cell, 0,
      (r + .5 - metrics.rows / 2) * metrics.cell)
  }

  // ---------------------------------------------------------------- 棋盘贴图
  function drawBoard(current, metrics) {
    const W = 1024
    const H = Math.max(256, Math.round(W * metrics.depth / metrics.width))
    const canvas = document.createElement('canvas')
    canvas.width = W; canvas.height = H
    const ctx = canvas.getContext('2d')
    if (!ctx) return null
    const sx = W / metrics.width, sz = H / metrics.depth
    const px = value => value * sx
    const pz = value => value * sz
    const wood = ctx.createLinearGradient(0, 0, W, H)
    wood.addColorStop(0, '#2c2519'); wood.addColorStop(.5, '#3a2f1f'); wood.addColorStop(1, '#241d14')
    ctx.fillStyle = wood
    ctx.fillRect(0, 0, W, H)

    if (!metrics.points) {
      for (let r = 0; r < metrics.rows; r += 1) for (let c = 0; c < metrics.cols; c += 1) {
        if (current.game === 'ttt') {
          ctx.fillStyle = '#17262e'; ctx.fillRect(px(c * metrics.cell), pz(r * metrics.cell),
            px(metrics.cell), pz(metrics.cell))
        } else {
          ctx.fillStyle = (r + c) % 2 ? '#3b4653' : '#cbbfa9'
          ctx.fillRect(px(c * metrics.cell), pz(r * metrics.cell), px(metrics.cell), pz(metrics.cell))
        }
      }
      if (current.game === 'ttt') {
        ctx.strokeStyle = '#5f7f90'; ctx.lineWidth = sx * .0025
        for (let i = 1; i < 3; i += 1) {
          ctx.beginPath(); ctx.moveTo(px(i * metrics.cell), 0); ctx.lineTo(px(i * metrics.cell), H); ctx.stroke()
          ctx.beginPath(); ctx.moveTo(0, pz(i * metrics.cell)); ctx.lineTo(W, pz(i * metrics.cell)); ctx.stroke()
        }
      }
      if (current.game === 'chess') {
        ctx.strokeStyle = '#0a1218'; ctx.lineWidth = 6
        ctx.strokeRect(3, 3, W - 6, H - 6)
      }
      return canvas
    }

    // 交叉点棋盘：木底线 + 网格
    const left = (metrics.width - (metrics.cols - 1) * metrics.cell) / 2
    const top = (metrics.depth - (metrics.rows - 1) * metrics.cell) / 2
    const lineX = c => px(left + c * metrics.cell)
    const lineZ = r => pz(top + r * metrics.cell)
    ctx.strokeStyle = current.game === 'xiangqi' ? '#c9ad7a' : '#c7b184'
    ctx.lineWidth = Math.max(1.4, sx * .0016)
    for (let c = 0; c < metrics.cols; c += 1) {
      ctx.beginPath()
      if (current.game === 'xiangqi' && c > 0 && c < metrics.cols - 1) {
        ctx.moveTo(lineX(c), lineZ(0)); ctx.lineTo(lineX(c), lineZ(4))
        ctx.moveTo(lineX(c), lineZ(5)); ctx.lineTo(lineX(c), lineZ(metrics.rows - 1))
      } else {
        ctx.moveTo(lineX(c), lineZ(0)); ctx.lineTo(lineX(c), lineZ(metrics.rows - 1))
      }
      ctx.stroke()
    }
    for (let r = 0; r < metrics.rows; r += 1) {
      ctx.beginPath(); ctx.moveTo(lineX(0), lineZ(r)); ctx.lineTo(lineX(metrics.cols - 1), lineZ(r)); ctx.stroke()
    }
    if (current.game === 'xiangqi') {
      ctx.save(); ctx.strokeStyle = '#d6bd8c'; ctx.lineWidth = Math.max(1.6, sx * .002)
      const x = c => lineX(c), y = r => lineZ(r)
      for (const [a, b, cc, d] of [[3, 0, 5, 2], [5, 0, 3, 2], [3, 7, 5, 9], [5, 7, 3, 9]]) {
        ctx.beginPath(); ctx.moveTo(x(a), y(b)); ctx.lineTo(x(cc), y(d)); ctx.stroke()
      }
      ctx.fillStyle = '#d8c49a'; ctx.textAlign = 'center'; ctx.textBaseline = 'middle'
      ctx.font = `600 ${Math.round(sx * .023)}px "Noto Serif SC", "Microsoft YaHei", serif`
      ctx.fillText('楚 河', (lineX(2) + lineX(1)) / 2 + sx * .002, (lineZ(4) + lineZ(5)) / 2)
      ctx.fillText('汉 界', (lineX(7) + lineX(6)) / 2 - sx * .002, (lineZ(4) + lineZ(5)) / 2)
      ctx.restore()
    }
    const stars = current.game === 'gomoku' ? [[3, 3], [3, 11], [11, 3], [11, 11], [7, 7]]
      : current.game === 'go' ? [[2, 2], [2, 6], [6, 2], [6, 6], [4, 4]] : []
    ctx.fillStyle = '#d6c398'
    for (const [r, c] of stars) {
      if (r >= metrics.rows || c >= metrics.cols) continue
      ctx.beginPath(); ctx.arc(lineX(c), lineZ(r), Math.max(2.2, sx * .0028), 0, Math.PI * 2); ctx.fill()
    }
    ctx.strokeStyle = '#0b1319'; ctx.lineWidth = 8
    ctx.strokeRect(2, 2, W - 4, H - 4)
    return canvas
  }

  function rebuildBoard(current, metrics) {
    if (boardMesh) {
      boardGroup.remove(boardMesh)
      boardMesh.geometry.dispose()
      boardMesh.material.map?.dispose()
      boardMesh.material.dispose()
    }
    if (standMesh) {
      boardGroup.remove(standMesh)
      standMesh.geometry.dispose()
      standMesh.material.dispose()
    }
    // 棋盘不再贴地：台面垫高到 BOARD_LIFT，下面给一块略大的木色低台，
    // 让棋盘看起来像放在工位桌上，而不是铺在地上的贴图。
    const stand = new THREE.BoxGeometry(physicalLayout.board_size_mm[1] / 1000, physicalLayout.board_top_mm / 1000, physicalLayout.board_size_mm[0] / 1000)
    standMesh = new THREE.Mesh(stand, new THREE.MeshStandardMaterial({
      color: 0x2b241b, roughness: .9, metalness: .05,
    }))
    standMesh.position.y = -physicalLayout.board_top_mm / 2000
    boardGroup.add(standMesh)
    const canvas = drawBoard(current, metrics)
    if (!canvas) return
    const texture = new THREE.CanvasTexture(canvas)
    texture.colorSpace = THREE.SRGBColorSpace
    texture.anisotropy = 8
    const geometry = new THREE.PlaneGeometry(metrics.width, metrics.depth)
    geometry.rotateX(-Math.PI / 2)
    const material = new THREE.MeshStandardMaterial({ map: texture, roughness: .82, metalness: .04, polygonOffset:true,polygonOffsetFactor:-1,polygonOffsetUnits:-1 })
    boardMesh = new THREE.Mesh(geometry, material)
    boardMesh.position.y = 0
    boardGroup.add(boardMesh)
    rebuildTray(current)
  }

  function clearTray() {
    const geometries = new Set(), materials = new Set()
    trayGroup.traverse(o => { if (o.geometry) geometries.add(o.geometry); if (o.material) materials.add(o.material) })
    trayGroup.clear()
    geometries.forEach(g => g.dispose()); materials.forEach(m => { m.map?.dispose(); m.dispose() })
  }
  function rebuildTray(current) {
    clearTray()
    const l = physicalLayout
    const center = root.worldToLocal(baseToGameScene(l.tray_center_mm))
    trayGroup.position.copy(center)
    const width=l.tray_size_mm[1]/1000, depth=l.tray_size_mm[0]/1000, height=l.tray_top_mm/1000
    const darkWood=['go','gomoku','xiangqi'].includes(current.game)
    const bodyMat=new THREE.MeshStandardMaterial({color:darkWood?0x32251e:0x172a35,roughness:.58,metalness:darkWood?.04:.3})
    const rimMat=new THREE.MeshStandardMaterial({color:current.game==='ttt'?0x289bad:darkWood?0x967147:0x8b9ca9,roughness:.4,metalness:.36})
    const floorMat=new THREE.MeshStandardMaterial({color:darkWood?0x594935:0x253b45,roughness:.8,metalness:.03})
    const add=(size,xyz,mat)=>{const m=new THREE.Mesh(new THREE.BoxGeometry(...size),mat);m.position.set(...xyz);trayGroup.add(m);return m}
    add([width,height,depth],[0,-height/2,0],bodyMat)
    add([width-.012,.001,depth-.012],[0,.0005,0],floorMat)
    const rw=l.rim_width_mm/1000,rh=l.rim_height_mm/1000
    add([width,rh,rw],[0,rh/2,depth/2-rw/2],rimMat)
    add([width,rh,rw],[0,rh/2,-depth/2+rw/2],rimMat)
    add([rw,rh,depth-rw*2],[width/2-rw/2,rh/2,0],rimMat)
    add([rw,rh,depth-rw*2],[-width/2+rw/2,rh/2,0],rimMat)
    // Thin inlays mark the actual pickup pitch; they sit flush and add no fake obstacle.
    const lines=[],pitch=l.tray_pitch_mm/1000
    for(let r=0;r<l.tray_rows;r++)for(let c=0;c<l.tray_cols;c++){
      const x=(c-(l.tray_cols-1)/2)*pitch,z=(r-(l.tray_rows-1)/2)*pitch,d=pitch*.32
      lines.push(x-d,.0011,z,x+d,.0011,z,x,.0011,z-d,x,.0011,z+d)
    }
    const geo=new THREE.BufferGeometry();geo.setAttribute('position',new THREE.Float32BufferAttribute(lines,3))
    trayGroup.add(new THREE.LineSegments(geo,new THREE.LineBasicMaterial({color:darkWood?0xb39469:0x59a0af,transparent:true,opacity:.32})))
  }

  // ---------------------------------------------------------------- 棋子模型
  function loadTemplates() {
    if (templates) return Promise.resolve(templates)
    if (templateJob) return templateJob
    templateJob = new Promise((resolve, reject) => {
      import('three/addons/loaders/GLTFLoader.js').then(({ GLTFLoader }) => {
        const loader = new GLTFLoader()
        loader.load(CHESS_MODEL_URL, gltf => {
          const built = new Map()
          for (const [code, spec] of Object.entries(CHESS_NODE)) {
            for (const color of ['W', 'B']) {
              const source = gltf.scene.getObjectByName(spec.name(color))
              if (!source) continue
              const holder = new THREE.Group()
              const clone = source.clone(true)
              clone.traverse(object => {
                if (object.isMesh) object.material = color === 'W' ? mats.white : mats.black
              })
              clone.updateMatrixWorld(true)
              const box = new THREE.Box3().setFromObject(clone)
              if (box.isEmpty()) continue
              const center = box.getCenter(new THREE.Vector3())
              clone.position.x -= center.x
              clone.position.z -= center.z
              clone.position.y -= box.min.y
              holder.add(clone)
              holder.userData.height = Math.max(.001, box.max.y - box.min.y)
              built.set(`${code}${color}`, { holder, height: spec.height })
            }
          }
          if (!built.size) throw new Error('棋子模型没有可用网格')
          templates = built
          resolve(built)
        }, undefined, reject)
      }).catch(reject)
    })
    return templateJob
  }

  function makeChessPiece(code, color, metrics) {
    const template = templates?.get(`${code}${color}`) || templates?.get(`${code}${color === 'W' ? 'B' : 'W'}`)
    if (!template) return null
    const node = template.holder.clone(true)
    node.scale.setScalar(template.height / template.holder.userData.height)
    return node
  }

  const stoneGeo = new THREE.LatheGeometry([
    new THREE.Vector2(0, 0), new THREE.Vector2(.35, 0), new THREE.Vector2(.72, .015),
    new THREE.Vector2(.92, .055), new THREE.Vector2(1, .135), new THREE.Vector2(.9, .225),
    new THREE.Vector2(.6, .29), new THREE.Vector2(0, .31),
  ], 40)
  const discGeo = new THREE.LatheGeometry([
    new THREE.Vector2(0, 0), new THREE.Vector2(.9, 0), new THREE.Vector2(1, .06),
    new THREE.Vector2(.97, .26), new THREE.Vector2(.9, .3), new THREE.Vector2(0, .3),
  ], 40)

  function textMaterial(glyph, side) {
    const key = `${glyph}:${side}`
    if (textMaterials.has(key)) return textMaterials.get(key)
    const canvas = document.createElement('canvas')
    canvas.width = canvas.height = 128
    const ctx = canvas.getContext('2d')
    if (!ctx) return null
    ctx.clearRect(0, 0, 128, 128)
    ctx.font = '700 86px "Noto Serif SC", "Microsoft YaHei", serif'
    ctx.textAlign = 'center'; ctx.textBaseline = 'middle'
    ctx.fillStyle = side === 'human' ? '#b23a32' : '#1d242b'
    ctx.fillText(glyph || '', 64, 68)
    const texture = new THREE.CanvasTexture(canvas)
    texture.colorSpace = THREE.SRGBColorSpace
    const material = new THREE.MeshBasicMaterial({ map: texture, transparent: true, depthWrite: false })
    textMaterials.set(key, material)
    return material
  }

  function makePiece(piece, metrics) {
    const node = new THREE.Group()
    if (state.game === 'chess') {
      const built = makeChessPiece(piece.code.toUpperCase(), piece.code === piece.code.toUpperCase() ? 'W' : 'B', metrics)
      if (!built) return null
      node.add(built)
      return node
    }
    if (state.game === 'gomoku' || state.game === 'go') {
      const mesh = new THREE.Mesh(stoneGeo, piece.code === 'black' ? mats.stoneBlack : mats.stoneWhite)
      mesh.scale.set(state.game==='go'?.01:.011,.008/.31,state.game==='go'?.01:.011)
      node.add(mesh)
    } else if (state.game === 'xiangqi') {
      const disc = new THREE.Mesh(discGeo, mats.disc)
      disc.scale.set(.015,.007/.3,.015)
      node.add(disc)
      const glyph = piece.glyph
      const material = textMaterial(glyph, piece.side)
      if (material) {
        const text = new THREE.Mesh(textGeo, material)
        text.scale.setScalar(metrics.cell * .8)
        text.position.y = .008
        node.add(text)
      }
    } else if (state.game === 'ttt') {
      const radius = metrics.cell * .3
      if (piece.code === 'O') {
        const torus = new THREE.Mesh(tttRingGeo,
          piece.side === 'human' ? mats.accent : mats.last)
        torus.rotation.x = -Math.PI / 2
        torus.position.y = .009
        node.add(torus)
      } else {
        const barGeo = tttBarGeo
        for (const angle of [Math.PI / 4, -Math.PI / 4]) {
          const bar = new THREE.Mesh(barGeo, piece.side === 'human' ? mats.accent : mats.last)
          bar.rotation.y = angle
          bar.position.y=.009
          node.add(bar)
        }
      }
    }
    return node
  }

  function clearPieces() {
    for (const piece of pieces) pieceGroup.remove(piece.node)
    for (const item of removals) pieceGroup.remove(item.node)
    pieces.length = 0; removals.length = 0
  }

  function syncPieces(current, metrics) {
    if (!current || current.game !== 'chess' || templates) {
      applyPieces(current, metrics)
      syncPhysical()
      return
    }
    loadTemplates().then(() => {
      if (state === current) {
        applyPieces(current, metrics)
        syncPhysical()
      }
    }).catch(() => { /* 模型缺失时保留 2D 棋盘，不伪造棋子 */ })
  }

  function applyPieces(current, metrics) {
    if (!current || disposed) return
    const old = new Map(pieces.map(piece => [`${piece.r}:${piece.c}`, piece]))
    const remaining = new Set(pieces)
    const targets = (current.cells || []).map(cell => ({
      r: cell.r, c: cell.c, code: cell.piece.code, side: cell.piece.side, glyph: cell.piece.glyph,
    }))
    const exact = []
    for (const target of targets) {
      const key = `${target.r}:${target.c}`
      const found = old.get(key)
      if (found && found.code === target.code && found.side === target.side) {
        exact.push(target)
        remaining.delete(found)
      }
    }
    const exactKeys = new Set(exact.map(target => `${target.r}:${target.c}`))
    const rest = targets.filter(target => !exactKeys.has(`${target.r}:${target.c}`))
    // 同兵种、同阵营按最近距离匹配：易位、吃过路兵、普通走子都能连续动画。
    for (const target of rest) {
      const targetPos = pointFor(metrics, target.r, target.c)
      let best = null, bestDistance = Infinity
      for (const piece of remaining) {
        if (piece.code !== target.code || piece.side !== target.side) continue
        const distance = piece.node.position.distanceToSquared(targetPos)
        if (distance < bestDistance) { best = piece; bestDistance = distance }
      }
      if (best) {
        remaining.delete(best)
        best.anim = { from: best.node.position.clone(), to: targetPos, t: 0, duration: .45, arc: .04 }
        best.r = target.r; best.c = target.c
      } else {
        spawnPiece(target, metrics)
      }
    }
    for (const piece of remaining) {
      removals.push({ node: piece.node, t: 0, duration: .24, scale: piece.node.scale.x || 1 })
      const index = pieces.indexOf(piece)
      if (index >= 0) pieces.splice(index, 1)
    }
  }

  function spawnPiece(target, metrics) {
    const node = makePiece(target, metrics)
    if (!node) return
    node.position.copy(pointFor(metrics, target.r, target.c))
    node.scale.setScalar(.001)
    node.userData.spawn = { t: 0, duration: .26 }
    pieceGroup.add(node)
    pieces.push({ ...target, node, anim: null })
  }

  // ---------------------------------------------------------------- 实体取放
  // 机械臂搬运时，用实体工件 xyz 驱动同一枚可视化棋子；代理物体本身不渲染。
  const sceneToBoard = new THREE.Matrix4()
  function updateLocalFrame() {
    // Update the parent chain once, without revisiting every physical piece
    // for each coordinate conversion on a full supply tray.
    root.updateWorldMatrix(true, false)
    sceneToBoard.copy(root.matrixWorld).invert()
  }
  function localPoint(xyz) {
    return baseToGameScene(xyz).applyMatrix4(sceneToBoard)
  }

  function clearCarried() {
    for (const entry of carried.values()) pieceGroup.remove(entry.node)
    carried.clear()
  }

  function syncPhysical() {
    for (const piece of pieces) piece.node.visible = true
    if (!state || !metricsNow) {
      clearCarried()
      return
    }
    if(state.game==='chess' && !templates)return
    updateLocalFrame()
    const authoritative = sceneObjects.filter(o=>o.task?.mode==='board' && o.task.role==='piece' && o.task.game===state.game
      && (!state.physical_group || o.task.group===state.physical_group))
    // A cleared physical scene must not resurrect its old 2D pieces as meshes.
    if(state.physical_group && !sceneObjects.some(o=>o.task?.mode==='board' && o.task.group===state.physical_group)) {
      root.visible=false
      clearCarried()
      for(const piece of pieces)piece.node.visible=false
      return
    }
    root.visible=true
    const rows=authoritative.length ? authoritative.map(o=>({id:o.id,code:o.task.code,side:o.task.side,glyph:glyphFor(o.task.code),
      xyz:[o.xyz[0],o.xyz[1],o.xyz[2]-o.size[2]/2],halfHeight:o.size[2]/2000})) : physical
    if(authoritative.length) for(const piece of pieces)piece.node.visible=false
    const ids = new Set()
    for (const item of rows) {
      if (!item || typeof item.id !== 'string') continue
      ids.add(item.id)
      if (Array.isArray(item.from)) {
        const target = pointFor(metricsNow, item.from[0], item.from[1])
        for (const piece of pieces) {
          if (piece.r === item.from[0] && piece.c === item.from[1]
              && piece.node.position.distanceToSquared(target) < 1e-8) piece.node.visible = false
        }
      }
      let entry = carried.get(item.id)
      if (!entry || entry.code !== item.code || entry.side !== item.side) {
        if (entry) pieceGroup.remove(entry.node)
        const node = makePiece({ code: item.code, side: item.side, glyph: item.glyph || '' }, metricsNow)
        if (!node) { carried.delete(item.id); continue }
        entry = { node, code: item.code, side: item.side }
        carried.set(item.id, entry)
        pieceGroup.add(node)
      }
      if (Array.isArray(item.xyz) && item.xyz.length === 3) entry.node.position.copy(localPoint(item.xyz))
      entry.node.userData.physicalHalfHeight=item.halfHeight || 0
    }
    for (const [id, entry] of carried) {
      if (ids.has(id)) continue
      pieceGroup.remove(entry.node)
      carried.delete(id)
    }
  }

  function glyphFor(code) {
    const glyphs={K:'帥',A:'仕',E:'相',H:'傌',R:'俥',C:'炮',P:'兵',k:'將',a:'士',e:'象',h:'馬',r:'車',c:'砲',p:'卒'}
    return state?.game==='xiangqi' ? glyphs[code] || '' : code
  }
  function setSceneObjects(list) { sceneObjects=Array.isArray(list)?list:[]; syncPhysical() }
  function presentAttachment(attachment,matrix) {
    if(!attachment || !matrix)return
    const entry=carried.get(attachment.id)
    if(!entry)return
    const world=new THREE.Vector3().fromArray(attachment.local_m).applyMatrix4(matrix)
    updateLocalFrame();entry.node.position.copy(world.applyMatrix4(sceneToBoard))
    entry.node.position.y-=entry.node.userData.physicalHalfHeight || 0
  }

  function setPhysical(list) {
    physical = Array.isArray(list) ? list : []
    syncPhysical()
    return { ok: true, carried: carried.size }
  }

  // ---------------------------------------------------------------- 提示标记
  function clearMarkers() {
    while (markerGroup.children.length) markerGroup.remove(markerGroup.children[0])
    suggestMarker = null
    selectedMarker = null
  }

  function addRing(position, material, scale, name) {
    const marker = new THREE.Mesh(ringGeo, material)
    marker.position.copy(position)
    marker.position.y = .0015
    marker.scale.setScalar(scale)
    marker.name = name
    markerGroup.add(marker)
    return marker
  }

  function addDot(position, material, scale) {
    const marker = new THREE.Mesh(dotGeo, material)
    marker.position.copy(position)
    marker.position.y = .0015
    marker.scale.setScalar(scale)
    markerGroup.add(marker)
  }

  function buildMarkers(current, metrics) {
    clearMarkers()
    if (!current) return
    const last = current.lastMove
    if (last?.from) addRing(pointFor(metrics, last.from[0], last.from[1]), mats.lastFrom, metrics.cell * .46, 'last-from')
    if (last?.to) addRing(pointFor(metrics, last.to[0], last.to[1]), mats.last, metrics.cell * .52, 'last')
    for (const [r, c] of current.legal || []) {
      const target = pointFor(metrics, r, c)
      addDot(target, mats.accent, metrics.cell * .22)
    }
    for (const item of current.illegal || []) {
      if (!item?.to) continue
      addDot(pointFor(metrics, item.to[0], item.to[1]), mats.danger, metrics.cell * .2)
    }
    if (current.selected) {
      selectedMarker = addRing(pointFor(metrics, current.selected[0], current.selected[1]), mats.select,
        metrics.cell * .5, 'selected')
    }
    if (current.check) {
      addRing(pointFor(metrics, current.check[0], current.check[1]), mats.check, metrics.cell * .55, 'check')
    }
    if (current.suggest?.to) {
      suggestMarker = addRing(pointFor(metrics, current.suggest.to[0], current.suggest.to[1]), mats.suggest,
        metrics.cell * .58, 'suggest')
      suggestMarker.userData.baseScale = metrics.cell * .58
    }
  }

  // ---------------------------------------------------------------- 对外
  function setState(next) {
    if (!next) {
      state = null
      root.visible = false
      clearPieces()
      clearCarried()
      clearMarkers()
      return { ok: true, visible: false }
    }
    const layout=readBoardLayout(next.physical_layout || next.physics_layout,next.game)
    if(!layout){root.visible=false;return {ok:false,why:'棋盘位置尚未就绪'}}
    state = next; physicalLayout=layout
    root.position.copy(layout.center);root.quaternion.setFromRotationMatrix(layout.basis);root.updateMatrixWorld(true)
    root.visible = true
    const metrics = cellMetrics(next)
    metricsNow = metrics
    const key = JSON.stringify([next.game,next.rows,next.cols,next.layout,next.physical_layout || next.physics_layout])
    if (key !== boardKey) {
      boardKey = key
      rebuildBoard(next, metrics)
    }
    buildMarkers(next, metrics)
    // syncPieces owns the physical synchronization, including deferred chess templates.
    syncPieces(next, metrics)
    return { ok: true, visible: root.visible, pieces: pieces.length, cell: metrics.cell }
  }

  function update(dt) {
    time += dt
    for (const piece of pieces) {
      if (piece.anim) {
        piece.anim.t = Math.min(1, piece.anim.t + dt / piece.anim.duration)
        const k = ease(piece.anim.t)
        piece.node.position.lerpVectors(piece.anim.from, piece.anim.to, k)
        piece.node.position.y += Math.sin(k * Math.PI) * piece.anim.arc
        if (piece.anim.t >= 1) {
          piece.node.position.copy(piece.anim.to)
          piece.anim = null
        }
      }
      if (piece.node.userData.spawn) {
        const spawn = piece.node.userData.spawn
        spawn.t = Math.min(1, spawn.t + dt / spawn.duration)
        piece.node.scale.setScalar(Math.max(.001, ease(spawn.t)))
        if (spawn.t >= 1) delete piece.node.userData.spawn
      }
    }
    for (let index = removals.length - 1; index >= 0; index -= 1) {
      const item = removals[index]
      item.t = Math.min(1, item.t + dt / item.duration)
      item.node.scale.setScalar(Math.max(.001, item.scale * (1 - ease(item.t))))
      if (item.t >= 1) {
        pieceGroup.remove(item.node)
        removals.splice(index, 1)
      }
    }
    if (suggestMarker) {
      const pulse = .5 + .5 * Math.sin(time * 3.2)
      suggestMarker.material.opacity = .55 + .35 * pulse
      suggestMarker.scale.setScalar(suggestMarker.userData.baseScale * (1 + .08 * pulse))
    }
    if (selectedMarker) selectedMarker.material.opacity = .65 + .35 * (0.5 + 0.5 * Math.sin(time * 2.4))
  }

  function dispose() {
    disposed=true
    parent.remove(root)
    clearTray()
    boardMesh?.geometry.dispose()
    boardMesh?.material.map?.dispose()
    boardMesh?.material.dispose()
    standMesh?.geometry.dispose()
    standMesh?.material.dispose()
    if (templates) {
      for (const template of templates.values()) {
        template.holder.traverse(object => object.geometry?.dispose())
      }
    }
    for (const material of Object.values(mats)) { material.map?.dispose(); material.dispose() }
    for (const material of textMaterials.values()) { material.map?.dispose(); material.dispose() }
    ringGeo.dispose(); dotGeo.dispose(); textGeo.dispose()
    stoneGeo.dispose(); discGeo.dispose();tttRingGeo.dispose();tttBarGeo.dispose()
    clearPieces(); clearCarried(); clearMarkers()
  }

  return {
    setState,
    setPhysical,
    setSceneObjects,
    presentAttachment,
    update,
    dispose,
    animating() {
      return removals.length > 0 || pieces.some(piece => piece.anim || piece.node.userData.spawn)
    },
    center() { return [root.position.x, root.position.y + .03, root.position.z] },
    info() { return { visible: root.visible, pieces: pieces.length, physicalPieces:carried.size, board: boardKey,layout:physicalLayout,position:root.position.toArray() } },
  }
}
