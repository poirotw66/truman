/* 共用像素美術：地圖圖磚 + 人物畫法。
   回放頁（replay/template.html）與人物工作室（cast/editor.template.html）都注入這一支，
   兩邊看起來才會是同一個世界。純函式、無 DOM 依賴（除了 canvas）。

   對外：
     ART                              一格 = 16 個美術像素
     buildWorldCanvas(rows, areas, TS, decor?)   → 畫好的離屏 canvas
     DECOR_JIANGHU                    衡山城的固定裝飾（樹/井/紅氈/幌子）
     drawFigure(g, cx, footY, T, prof, opts)     人物（腳底站在 footY）
     drawPortrait(g, size, prof, opts)           方形頭像（小人縮圖）
     drawBust(g, size, prof, opts)               立繪：48×64 半身像，比例 3:4
   prof 欄位：robe trim hair hat(bun|official|nun|band|none) beard(long|short|stub|none)
              weapon(sword|blade|qin|hammer|null)
              acc(gourd|jade|kasaya|beads|rope|pouch|spyglass|whistle|null)
*/
"use strict";
const ART = 16;

function hash(x, y, s) {
  let n = (x * 73856093) ^ (y * 19349663) ^ ((s || 0) * 83492791);
  n = (n < 0 ? ~n : n) % 65521;
  return n / 65521;
}

const PAL = {
  street:["#c2a877","#b99f6e","#cbb181"], streetLine:"#a78d5f",
  wall:"#5d5648", wallTop:"#7b7360", wallDark:"#3b3529",
  plaza:["#96958a","#8d8c81","#9f9e92"],
  dirt:["#a08a5f","#977f55"],
  gate:"#6b6252",
};
/* 室內是「掀了屋頂」的畫法（人在屋裡就看得見人），牆畫在區塊邊緣，南面留門。 */
const INT = {
  l:{floor:"#b58f57", plank:"#9c7743", wall:"#7c4331", wallHi:"#a35a3c", kind:"wood"},
  t:{floor:"#a87c46", plank:"#8d6434", wall:"#6b4425", wallHi:"#8b5c33", kind:"wood"},
  p:{floor:"#8f6d70", plank:"#7b585e", wall:"#6b3b43", wallHi:"#8c4a54", kind:"wood"},
  c:{floor:"#8e8b7c", plank:"#7c7969", wall:"#5b5850", wallHi:"#767264", kind:"stone"},
  z:{floor:"#7b7566", plank:"#6a6456", wall:"#4c473b", wallHi:"#615c4c", kind:"stone", ruin:true},
};
const DECOR_JIANGHU = [
  {t:"carpet", x:2, y:4}, {t:"carpet", x:3, y:4}, {t:"carpet", x:4, y:4}, {t:"carpet", x:4, y:5},
  {t:"tree", x:6, y:4}, {t:"tree", x:19, y:4}, {t:"tree", x:6, y:13}, {t:"tree", x:17, y:12}, {t:"tree", x:14, y:8},
  {t:"well", x:8, y:12}, {t:"crate", x:16, y:5}, {t:"crate", x:9, y:13}, {t:"banner", x:17, y:4},
];

function buildWorldCanvas(rows, areas, TS, decor, margin) {
  const W = rows[0].length, H = rows.length, M = margin == null ? 4 : margin;
  const cv = document.createElement("canvas");
  cv.width = (W + M * 2) * TS; cv.height = (H + M * 2) * TS;
  cv.margin = M;                                    // blit 的時候要知道往外多畫了幾格
  const wx = cv.getContext("2d");
  wx.translate(M * TS, M * TS);                     // 之後所有座標都還是以城內 (0,0) 為原點
  const at = (x, y) => (x < 0 || y < 0 || x >= W || y >= H) ? "#" : rows[y][x];

  /* 同符號連通塊 → 知道自己在不在區塊邊緣、門開在哪一格 */
  const blockIdx = new Int16Array(W * H).fill(-1), blocks = [];
  for (let y = 0; y < H; y++) for (let x = 0; x < W; x++) {
    const s = at(x, y);
    if (s === "." || s === "#" || blockIdx[y*W+x] >= 0) continue;
    const id = blocks.length, q = [[x, y]], b = { sym:s, x0:x, y0:y, x1:x, y1:y };
    blockIdx[y*W+x] = id;
    while (q.length) {
      const [cx, cy] = q.pop();
      b.x0 = Math.min(b.x0,cx); b.x1 = Math.max(b.x1,cx); b.y0 = Math.min(b.y0,cy); b.y1 = Math.max(b.y1,cy);
      [[1,0],[-1,0],[0,1],[0,-1]].forEach(([dx,dy]) => {
        const nx = cx+dx, ny = cy+dy;
        if (nx<0 || ny<0 || nx>=W || ny>=H) return;
        if (at(nx,ny) === s && blockIdx[ny*W+nx] < 0) { blockIdx[ny*W+nx] = id; q.push([nx,ny]); }
      });
    }
    blocks.push(b);
  }
  const blockAt = (x, y) => blocks[blockIdx[y*W+x]];

  function paintTile(x, y) {
    const sym = at(x, y), T = TS, u = T / ART;
    const R = (ax, ay, aw, ah, c) => { wx.fillStyle = c; wx.fillRect(Math.round(x*T+ax*u), Math.round(y*T+ay*u), Math.max(1,Math.round(aw*u)), Math.max(1,Math.round(ah*u))); };
    const h = (s) => hash(x, y, s);

    if (sym === "#") {                                     // 城牆
      R(0,0,16,16,PAL.wall);
      for (let ry=0; ry<16; ry+=4) { R(0,ry,16,1,PAL.wallDark); const off = (ry/4)%2 ? 0 : 4; for (let rx=off; rx<16; rx+=8) R(rx,ry,1,4,PAL.wallDark); }
      if (at(x,y+1) !== "#") R(0,13,16,3,PAL.wallDark);
      if (at(x,y-1) !== "#") { R(0,0,16,3,PAL.wallTop); for (let rx=1; rx<16; rx+=6) R(rx,0,3,2,PAL.wall); }
      if (h(3) > .86) R(3,6,4,3,"#4d5a3c");                // 苔
      return;
    }
    if (INT[sym]) {                                        // 室內（掀頂）
      const p = INT[sym], b = blockAt(x,y);
      const N = y === b.y0, S = y === b.y1, WW = x === b.x0, E = x === b.x1;
      const doorX = Math.floor((b.x0 + b.x1) / 2), tall = b.y1 > b.y0;
      R(0,0,16,16,p.floor);
      if (p.kind === "wood") { for (let ry=3; ry<16; ry+=4) R(0,ry,16,1,p.plank); if (h(9)>.6) R(2,7,7,1,p.plank); }
      else { for (let ry=0; ry<16; ry+=8) R(0,ry,16,1,p.plank); for (let rx=0; rx<16; rx+=8) R(rx,0,1,16,p.plank); }
      if (p.ruin && h(2) > .5) R(3+Math.floor(h(3)*7), 5+Math.floor(h(4)*7), 3, 2, "#4a4438");
      if (p.ruin && h(5) > .72) R(9,9,4,3,"#4d6a3e");

      // 家什
      if (sym === "l") {                                              // 廳堂：紅氈、太師椅
        if (x === doorX) { R(4,0,8,16,"#9d3a2f"); R(4,0,1,16,"#7e2c23"); R(11,0,1,16,"#7e2c23"); }
        else if (!N && h(6) > .45) { R(3,6,4,5,"#6b4a2c"); R(3,5,4,2,"#8a6a3c"); }
      } else if (sym === "t") {                                       // 酒樓：酒桌
        if ((x + y) % 2 === 0 && !N) { R(4,6,8,6,"#7a5230"); R(5,5,6,2,"#94663c"); R(2,8,2,3,"#5d3a1c"); R(12,8,2,3,"#5d3a1c"); R(6,7,2,2,"#e6dcc4"); }
      } else if (sym === "p") {                                       // 院子：屏風、坐榻
        if (h(7) > .55 && !N) { R(2,5,12,4,"#7a4b52"); R(2,5,12,1,"#a2727a"); }
        if (h(8) > .8) R(5,10,6,4,"#63373d");
      } else if (sym === "c") {                                       // 廟：蒲團
        if (h(6) > .6 && !N) { R(5,8,6,4,"#9a8a5c"); R(5,8,6,1,"#b7a675"); }
      }

      // 牆
      if (N) {
        R(0,0,16,5,p.wall); R(0,4,16,1,p.wallHi);
        if (sym === "l" && x === doorX) { R(3,0,10,4,"#8c2b22"); R(3,0,10,1,"#d9a63c"); R(3,3,10,1,"#d9a63c"); R(6,1,4,2,"#d9a63c"); }
        else if (sym === "t") { R(1,0,14,4,"#5d3a1c"); for (let rx=2; rx<14; rx+=3) { R(rx,1,2,3,"#8a6a3c"); R(rx,1,2,1,"#c8a02e"); } }
        else if (sym === "p") { for (let rx=0; rx<16; rx+=3) R(rx,0,2,5,"#7d444c"); R(0,4,16,1,"#d9a63c"); }
        else if (sym === "c" && x === doorX) { R(5,0,6,5,"#b79a4c"); R(6,1,4,2,"#d9c07a"); R(6,3,4,2,"#8c7433"); }
        else if (p.ruin) { if (h(1) > .5) R(4,1,6,4,p.floor); }
      }
      if (WW) { R(0,0,2,16,p.wall); R(2,0,1,16,p.wallHi); }
      if (E) { R(14,0,2,16,p.wall); R(13,0,1,16,p.wallHi); }
      if (S && tall) {
        if (x === doorX) { R(0,14,4,2,p.wall); R(12,14,4,2,p.wall); R(0,13,4,1,p.wallHi); R(12,13,4,1,p.wallHi); }
        else { R(0,14,16,2,p.wall); R(0,13,16,1,p.wallHi); }
      }
      R(0,0,16,16,"rgba(30,18,6,.10)");                    // 室內壓暗一點
      return;
    }
    if (sym === "m") {                                     // 市集：北面布篷，其餘是攤位走道
      const b = blockAt(x,y);
      R(0,0,16,16,"#b09363");
      for (let i=0; i<3; i++) if (hash(x,y,40+i) > .5) R(1+Math.floor(h(9+i)*11), 2+Math.floor(h(19+i)*12), 3, 2, "#9c8052");
      if (y === b.y0) {
        const A = ["#cdd6d8","#c05a4a","#dcc37a"][Math.floor(h(9)*3)];
        R(0,0,16,9,"#f2ead6");
        for (let rx=0; rx<16; rx+=4) R(rx,0,2,9,A);
        R(0,8,16,2,"#6d5836"); R(0,0,16,1,"#6d5836");
      } else {
        R(1,3,14,5,"#8a6a3c"); R(1,3,14,1,"#a5814a"); R(1,7,14,1,"#5e4526");
        const g = ["#b8823a","#8fa05a","#c0574a","#d9c07a"][Math.floor(h(11)*4)];
        R(3,4,3,2,g); R(8,4,2,2,"#d9c07a"); R(11,4,3,2,g);
        R(2,11,2,4,"#6d5836"); R(12,11,2,4,"#6d5836");
      }
      return;
    }
    if (sym === "s") {                                     // 演武場青石
      R(0,0,16,16,PAL.plaza[Math.floor(h(2)*3)]);
      R(0,0,16,1.2,"#7d7c72"); R(0,0,1.2,16,"#7d7c72");
      if (h(7) > .8) R(4,5,7,4,"#8a8a7e");
      if (h(3) > .9) R(9,10,4,2,"#a9a89b");
      return;
    }
    if (sym === "g") {                                     // 城門洞：石地，東面是包鐵大門
      const b = blockAt(x,y);
      R(0,0,16,16,PAL.gate);
      for (let ry=0; ry<16; ry+=5) R(0,ry,16,1,"#544c3e");
      if (y === b.y0) { R(0,0,16,4,"#4a4337"); R(0,3,16,1,"#7b7360"); }
      if (y === b.y1) R(0,13,16,3,"#4a4337");
      if (x === b.x1) {
        R(9,0,7,16,"#4a3627"); R(9,0,1,16,"#6b5238");
        for (let ry=1; ry<16; ry+=4) { R(10,ry,6,1,"#33261a"); R(11,ry,2,2,"#8d8676"); }
      }
      return;
    }
    if (sym === "y") {                                     // 後院泥地
      R(0,0,16,16,PAL.dirt[h(2)>.5?0:1]);
      if (h(3) > .74) { R(2,7,10,6,"#7a5c33"); for (let ry=8; ry<13; ry+=2) R(2,ry,10,1,"#5e4526"); }
      else if (h(4) > .62) { R(5,6,6,8,"#6d6355"); R(5,5,6,2,"#57503f"); }
      else if (h(5) > .5) R(6,9,5,4,"#4d5a3c");
      return;
    }
    if (sym === "~") {                                     // 海／洪水（嵐潮）
      const SEA = ["#3a6d8a", "#356480", "#407897"];
      R(0,0,16,16,SEA[Math.floor(h(2)*3)]);
      R(0, Math.floor(h(4)*6)+2, 16, 1, "rgba(220,240,255,.22)");
      R(0, Math.floor(h(5)*8)+6, 16, 1, "rgba(180,220,240,.16)");
      if (h(6) > .7) R(Math.floor(h(7)*10)+2, Math.floor(h(8)*10)+2, 3, 2, "rgba(255,255,255,.18)");
      return;
    }
    // 長街
    R(0,0,16,16,PAL.street[Math.floor(h(1)*3)]);
    for (let i=0; i<4; i++) { const cx = Math.floor(h(10+i)*12)+1, cy = Math.floor(h(20+i)*12)+1;
      if (hash(x,y,30+i) > .45) R(cx,cy,3,2,PAL.streetLine); }
    if (h(6) > .88) { R(6,7,4,3,"#6e7a45"); R(7,6,2,2,"#7f8c4e"); }
  if (at(x,y-1) === "#") { R(0,0,16,2,"#5f6b3e"); for (let rx=1; rx<15; rx+=4) R(rx,1,2,2,"#6e7a45"); }
    if (h(8) > .96) R(3,3,2,2,"#a89b78");
  }

  /* 牆與屋身往南邊投影，讓建築離地 */
  function paintShadows() {
    const T = TS, u = T / ART;
    const solid = s => s === "#" || (INT[s] !== undefined);
    for (let y=0; y<H; y++) for (let x=0; x<W; x++) {
      const me = at(x,y), up = at(x,y-1);
      if (me === up || !solid(up) || me === "#") continue;
      if (INT[up] && y - 1 !== blockAt(x,y-1).y1) continue;   // 只有屋子最南那排（有牆）才投影
      wx.fillStyle = "rgba(20,14,6,.26)";
      wx.fillRect(Math.round(x*T), Math.round(y*T), T, Math.round(4*u));
    }
  }

  function paintDecor(list) {
    const T = TS, u = T / ART;
    const at2 = (x,y) => (ax,ay,aw,ah,c) => { wx.fillStyle=c; wx.fillRect(Math.round(x*T+ax*u),Math.round(y*T+ay*u),Math.max(1,Math.round(aw*u)),Math.max(1,Math.round(ah*u))); };
    list.forEach(d => {
      const R = at2(d.x, d.y);
      if (d.t === "carpet") { R(0,0,16,16,"#9d3a2f"); R(0,0,16,2,"#7e2c23"); R(0,14,16,2,"#7e2c23"); R(2,7,12,2,"#c1a04a"); }
      else if (d.t === "tree") { R(6,10,4,5,"#5b4325"); R(2,2,12,9,"#3f6b3a"); R(3,1,10,3,"#4d7f45"); R(5,4,5,3,"#5b9450"); R(4,9,8,2,"#31552d"); }
      else if (d.t === "well") { R(3,5,10,9,"#7a7364"); R(4,6,8,7,"#3a4348"); R(3,4,10,2,"#8d8676"); R(5,1,2,5,"#5b4325"); R(9,1,2,5,"#5b4325"); R(4,0,8,2,"#5b4325"); }
      else if (d.t === "crate") { R(3,6,10,8,"#8a6a3c"); R(3,9,10,1,"#5e4526"); R(7,6,1,8,"#5e4526"); }
      else if (d.t === "banner") { R(7,0,2,10,"#5b4325"); R(9,1,6,9,"#c8a02e"); R(9,3,6,1,"#8c6a15"); R(9,6,6,1,"#8c6a15"); }
    });
  }

  /* 城外：沒有這一圈，城牆外面就是一片黑，看起來像地圖沒畫完。
     遠山在北、田在西南、林在東南、出東門的路一直通到畫面外。 */
  function paintOutside() {
    const T = TS, u = T / ART;
    const R = (tx, ty, ax, ay, aw, ah, c) => { wx.fillStyle = c;
      wx.fillRect(Math.round(tx*T+ax*u), Math.round(ty*T+ay*u), Math.max(1,Math.round(aw*u)), Math.max(1,Math.round(ah*u))); };
    const GRASS = ["#6f7a45","#788350","#66713f"], FIELD = ["#9d8b4e","#8d7c44"];
    for (let y = -M; y < H + M; y++) for (let x = -M; x < W + M; x++) {
      if (x >= 0 && y >= 0 && x < W && y < H) continue;
      const h = (s) => hash(x + 99, y + 99, s);
      R(x, y, 0, 0, 16, 16, GRASS[Math.floor(h(1) * 3)]);
      if (h(2) > .82) { R(x, y, 4, 6, 3, 2, "#5b6738"); R(x, y, 9, 10, 3, 2, "#5b6738"); }

      const southWestField = y >= H && x < W * .55;               // 西南：水田
      const southEastWood = y >= H && x >= W * .55;           // 東南：林子
      const eastWood = x >= W && y < H - 2 && y > 2;
      if (y <= -2) {                                             // 遠山：整片深色的量體
        R(x, y, 0, 0, 16, 16, "#3d5148");
        R(x, y, 0, 6 + Math.round(3 * Math.sin(x * .7)), 16, 3, "#455a4f");
      } else if (y === -1) {                                     // 近山：起伏的稜線
        const ridge = 7 + Math.round(3.4 * Math.sin(x * .55) + 2.2 * Math.sin(x * .23 + 1.7));
        R(x, y, 0, ridge, 16, 16 - ridge, "#4c6354");
        R(x, y, 0, ridge, 16, 2, "#5c7660");                     // 迎光面
        R(x, y, 6, ridge + 2, 4, 16 - ridge - 2, "#44594c");     // 山溝
        R(x, y, 0, 13, 16, 3, "rgba(228,232,214,.22)");          // 山腳的霧
      } else if (southWestField) {                               // 水田：一格水一格旱，中間有田埂
        const wet = ((x + y) % 2) === 0;
        R(x, y, 0, 0, 16, 16, wet ? "#6d8a72" : FIELD[h(5) > .5 ? 0 : 1]);
        if (wet) { for (let ry = 3; ry < 15; ry += 3) R(x, y, 2, ry, 12, 1, "#87a189"); }
        else { for (let ry = 2; ry < 16; ry += 4) R(x, y, 1, ry, 14, 1, "#7d6d38"); }
        R(x, y, 0, 0, 16, 1.4, "#8d7c50"); R(x, y, 0, 0, 1.4, 16, "#8d7c50");   // 田埂
      } else if (southEastWood || eastWood) {
        if (h(6) > .38) { R(x, y, 5, 10, 3, 4, "#4d3a20"); R(x, y, 1, 2, 11, 9, "#3b6337");
          R(x, y, 2, 1, 9, 3, "#487642"); R(x, y, 4, 4, 4, 3, "#57894e"); }
      }
    }
    // 出東門的官道，一路通到畫面外
    for (let x = W; x < W + M; x++) for (let y = 6; y <= 7; y++) {
      R(x, y, 0, 0, 16, 16, "#b9a06f");
      for (let i = 0; i < 3; i++) if (hash(x, y, 50 + i) > .5) R(x, y, 2 + i * 5, 4 + Math.floor(hash(x,y,60+i)*8), 3, 2, "#a78d5f");
    }
    // 城牆外緣的暗邊，讓城體和郊野分開
    wx.fillStyle = "rgba(20,14,6,.3)";
    wx.fillRect(-Math.round(TS*.12), H * TS, W * TS + Math.round(TS*.24), Math.round(TS * .18));
  }
  paintOutside();
  for (let y=0; y<H; y++) for (let x=0; x<W; x++) paintTile(x,y);
  paintShadows();
  paintDecor(decor || []);
  // 區域名：貼在區域上緣（北牆下方），免得壓到站在屋子中間的人
  wx.textAlign = "center"; wx.textBaseline = "middle";
  wx.font = `600 ${Math.round(TS*0.38)}px "Songti SC",serif`;
  (areas || []).forEach(a => {
    const cx = ((a.x0+a.x1)/2+0.5)*TS, cy = a.y0*TS + TS*0.74;
    wx.fillStyle = "rgba(10,8,4,.5)"; wx.fillText(a.name, cx, cy+2);
    wx.fillStyle = "rgba(255,246,224,.44)"; wx.fillText(a.name, cx, cy);
  });
  return cv;
}

/* ---------------------------------------------------------------- 人物

   16×16 個美術像素，腳底站在 footY。畫法分兩趟：先把所有方塊往外撐 0.45 格畫成
   深色，再照原色畫一次——等於免費得到一圈描邊。沒有描邊的小人在地圖上會糊成一坨。

   prof：robe trim hair hat beard weapon acc(gourd|jade|kasaya|null)
*/
const SKIN = "#e8bd90", SKIN_D = "#c29268", DARK = "#181310", OUTLINE = "#171210";

function shade(hex, amt) {                       // amt<0 變暗、>0 變亮
  const n = parseInt(String(hex).replace("#", ""), 16);
  if (isNaN(n)) return hex;
  const t = amt < 0 ? 0 : 255, k = Math.abs(amt);
  const r = (n >> 16) & 255, g = (n >> 8) & 255, b = n & 255;
  const m = v => Math.round(v + (t - v) * k);
  return `rgb(${m(r)},${m(g)},${m(b)})`;
}

function drawFigure(g, cx, footY, T, prof, o) {
  o = o || {};
  const u = T / ART, face = o.face || "down", fr = o.frame || 0, hurt = o.wound || 0;
  const side = face === "left" || face === "right", flip = face === "left", back = face === "up";
  const ox = cx - 8 * u, oy = footY - 15.6 * u + (o.bob || 0) * u;

  // 地上的影子（不進描邊）
  g.fillStyle = "rgba(0,0,0,.32)";
  g.beginPath(); g.ellipse(cx, footY - .6 * u, 5 * u, 1.9 * u, 0, 0, 6.3); g.fill();

  if (o.dead) { drawFallen(g, cx, footY, T, prof); return; }

  const ops = [];
  const R = (ax, ay, aw, ah, c) => { if (aw > 0 && ah > 0) ops.push([flip ? 16 - ax - aw : ax, ay, aw, ah, c]); };

  const robe = prof.robe, robeD = shade(robe, -.28), robeL = shade(robe, .12);
  const trim = prof.trim, hair = prof.hair === "none" ? "#efe7d5" : prof.hair;
  const step = (fr === 1) ? 1 : (fr === 3 ? -1 : 0);        // 走路：左右腳交替

  /* ---- 頭 ---- */
  const hatH = prof.hat === "official" ? 1.4 : 0;
  R(5.5, 3.0, 5.0, 4.6, SKIN);                              // 臉
  R(5.5, 7.2, 5.0, .6, SKIN_D);                             // 下巴陰影
  R(6.9, 7.6, 2.2, 1.0, SKIN_D);                            // 脖子
  if (back) {
    R(5.2, 2.4, 5.6, 5.2, hair);                            // 背面：整顆頭都是頭髮
  } else {
    R(5.2, 2.4, 5.6, 1.6, hair);                            // 瀏海
    R(5.2, 3.6, .9, 2.6, hair); R(9.9, 3.6, .9, 2.6, hair); // 鬢角
    if (!side) { R(6.6, 5.3, .9, 1.0, DARK); R(8.5, 5.3, .9, 1.0, DARK); }   // 眼
    else { R(9.2, 5.3, .9, 1.0, DARK); R(10.5, 4.6, .7, 1.6, SKIN); }        // 側臉：一隻眼 + 鼻
  }
  if (prof.hat === "bun") { R(7.1, 1.0, 1.9, 1.6, hair); R(7.0, .7, 2.1, .6, trim); }
  else if (prof.hat === "official") { R(4.6, 1.0, 6.8, 1.8, "#241e17"); R(3.0, 1.5, 1.7, .9, "#241e17");
    R(11.3, 1.5, 1.7, .9, "#241e17"); R(7.4, 1.0, 1.2, 1.8, "#c8a02e"); R(5.0, 2.6, 6.0, .6, "#241e17"); }
  else if (prof.hat === "nun") { R(4.9, 2.0, 6.2, 2.0, "#f2ecdd"); R(4.9, 3.8, 1.0, 2.8, "#f2ecdd");
    R(10.1, 3.8, 1.0, 2.8, "#f2ecdd"); R(4.9, 2.0, 6.2, .6, "#d8cdb4"); }
  else if (prof.hat === "band") { R(5.0, 2.9, 6.0, 1.0, "#c8a02e"); R(5.0, 3.5, 6.0, .4, "#8c6a15"); }
  if (!back) {
    if (prof.beard === "long") { R(6.4, 7.4, 3.2, 3.0, "#ece6d8"); R(6.9, 10.2, 2.2, 1.4, "#ece6d8");
      R(5.9, 5.8, .8, 1.6, "#ece6d8"); R(9.3, 5.8, .8, 1.6, "#ece6d8"); }
    else if (prof.beard === "short") { R(6.6, 6.9, 2.8, .8, hair); R(7.2, 7.6, 1.6, .9, hair); }
    else if (prof.beard === "stub") { R(6.4, 6.9, 3.2, .8, shade(hair, .25)); }
  }

  /* ---- 身：肩窄、下擺寬，才不會是一塊磚 ---- */
  const bw = side ? 5.2 : 6.0, bx = 8 - bw / 2;
  R(bx, 8.4, bw, 4.3, robe);
  R(bx, 8.4, bw, .5, robeL);                                // 肩線打亮
  R(bx + bw - .6, 8.9, .6, 3.8, robeD);                     // 側面陰影
  if (!side && !back) {                                     // 交領：裡面的中衣露出一個 V
    R(6.7, 8.4, 1.0, 1.1, "#efe7d5"); R(8.3, 8.4, 1.0, 1.1, "#efe7d5");
    R(7.3, 9.3, 1.4, .8, "#efe7d5");
    R(6.2, 8.4, .6, 1.5, trim); R(9.2, 8.4, .6, 1.5, trim);
  } else if (back) { R(7.6, 8.4, .8, 4.3, robeD); }         // 背縫
  R(bx - .2, 11.3, bw + .4, .9, trim);                      // 腰帶
  R(7.3, 11.3, 1.4, .9, "#c8a02e");                         // 帶扣
  R(4.3, 12.5, 7.4, 1.8, robeD);                            // 下擺（比肩寬 → 梯形）
  R(4.3, 12.5, 7.4, .4, shade(robe, -.45));
  for (let i = 1; i < 3; i++) R(4.1 + i * 2.5, 12.8, .4, 1.5, shade(robe, -.42));

  /* ---- 袖與手 ---- */
  if (side) {
    R(9.8, 8.8, 1.9, 3.0, robeD); R(9.9, 11.5, 1.7, .6, trim); R(10.1, 12.0, 1.3, 1.1, SKIN);
  } else {
    R(3.6, 8.8, 1.8, 2.9, robeD); R(10.6, 8.8, 1.8, 2.9, robeD);
    R(3.6, 11.4, 1.8, .6, trim); R(10.6, 11.4, 1.8, .6, trim);
    R(3.8, 11.9, 1.4, 1.1, SKIN); R(10.8, 11.9, 1.4, 1.1, SKIN);
  }

  /* ---- 腳（露在下擺外面，不然整個人沒有底）---- */
  R(5.9, 14.2 - Math.max(0, step) * .4, 2.0, 1.3, "#3a2f26");
  R(8.1, 14.2 - Math.max(0, -step) * .4, 2.0, 1.3, "#3a2f26");

  /* ---- 兵器與隨身物（貼著身體，才不會像浮在旁邊的棍子）---- */
  if (prof.weapon === "sword") { R(11.6, 9.0, 1.0, 4.4, "#5e5a52"); R(11.4, 8.0, 1.5, 1.2, "#c8a02e");
    R(11.7, 13.2, .8, .8, "#c8a02e"); }
  else if (prof.weapon === "blade") { R(11.5, 8.6, 1.3, 5.0, "#3f4149"); R(11.3, 8.0, 1.8, .9, "#cfd4da");
    R(11.6, 13.4, 1.1, 1.0, "#8a3a2f"); }
  else if (prof.weapon === "qin") { R(2.2, 10.2, 4.4, 1.7, "#6b4a2c"); R(2.4, 10.4, 4.0, .4, "#d9c07a");
    R(2.4, 11.2, 4.0, .3, "#d9c07a"); }
  else if (prof.weapon === "hammer") { R(11.4, 9.6, 1.2, 3.6, "#3a342c"); R(10.6, 9.0, 2.8, 1.6, "#6a7078");
    R(10.8, 9.2, 2.4, .6, "#9aa3ad"); }
  if (prof.acc === "gourd") { R(11.4, 11.6, 1.8, 2.0, "#c08a3e"); R(11.8, 10.9, 1.0, .9, "#c08a3e");
    R(11.9, 10.5, .8, .5, "#6b4a2c"); }
  else if (prof.acc === "jade") { R(8.9, 12.2, 1.0, 1.3, "#7fc7b8"); R(9.2, 11.6, .4, .8, "#d9c07a"); }
  else if (prof.acc === "kasaya") { R(5.6, 8.6, 1.5, 1.4, "#b5762f"); R(6.6, 9.6, 1.5, 1.4, "#b5762f");
    R(7.6, 10.6, 1.5, 1.2, "#b5762f"); }
  else if (prof.acc === "beads") { R(3.6, 11.0, 1.0, 1.0, "#6b4a2c"); R(3.4, 11.8, 1.0, 1.0, "#8a6234");
    R(3.8, 12.6, 1.0, 1.0, "#6b4a2c"); }
  else if (prof.acc === "rope") { R(11.2, 8.6, 2.0, 1.4, "#8a6a3a"); R(11.4, 9.8, 1.6, 2.4, "#6b5230");
    R(11.6, 11.8, 1.2, 1.0, "#8a6a3a"); }
  else if (prof.acc === "pouch") { R(11.2, 11.4, 2.2, 2.2, "#5f7a4a"); R(11.4, 11.6, 1.8, 1.4, "#7a9a5a");
    R(11.8, 11.0, .8, .8, "#3a342c"); }
  else if (prof.acc === "spyglass") { R(11.2, 10.0, 1.2, 3.4, "#8a7038"); R(11.0, 9.6, 1.6, 1.0, "#c8a02e");
    R(11.4, 13.0, .8, .8, "#5a4a28"); }
  else if (prof.acc === "whistle") { R(8.6, 11.0, .6, 2.0, "#9aa3ad"); R(8.4, 10.6, 1.0, .8, "#cfd4da"); }

  /* ---- 傷 ---- */
  if (hurt >= 1) R(6.5, 10.0, 2.0, 1.1, "#a32c22");
  if (hurt >= 2) { R(bx + .2, 9.2, 1.3, 2.6, "#8c1f18"); R(9.0, 12.6, 2.0, 1.1, "#8c1f18");
    if (!back) R(9.7, 3.4, 1.1, .7, "#a32c22"); }          // 額角的傷，別畫在眼睛上

  /* ---- 兩趟畫：描邊 → 本體 ---- */
  const e = .45;
  const px = (ax, ay, aw, ah, c) => {
    g.fillStyle = c;
    g.fillRect(Math.round(ox + ax * u), Math.round(oy + ay * u),
               Math.max(1, Math.round(aw * u)), Math.max(1, Math.round(ah * u)));
  };
  ops.forEach(([ax, ay, aw, ah]) => px(ax - e, ay - e, aw + e * 2, ah + e * 2, OUTLINE));
  ops.forEach(([ax, ay, aw, ah, c]) => px(ax, ay, aw, ah, c));
}

function drawFallen(g, cx, footY, T, prof) {
  const u = T / ART, ox = cx - 8 * u, oy = footY - 7 * u;
  const ops = [];
  const R = (ax, ay, aw, ah, c) => ops.push([ax, ay, aw, ah, c]);
  const robe = prof.robe, robeD = shade(robe, -.28);
  const hair = prof.hat === "nun" ? "#f2ecdd" : (prof.hair === "none" ? "#efe7d5" : prof.hair);

  g.fillStyle = "rgba(122,20,14,.45)";
  g.beginPath(); g.ellipse(cx, footY - 1.2 * u, 6.8 * u, 2.6 * u, 0, 0, 6.3); g.fill();

  R(3.2, 2.6, 7.6, 3.6, robe);                      // 身子橫躺
  R(3.2, 5.4, 7.6, .8, robeD);
  R(4.6, 2.6, 1.0, 3.6, prof.trim);                 // 腰帶
  R(10.6, 2.4, 2.8, 3.2, SKIN);                     // 頭
  R(11.6, 2.0, 2.2, 1.6, hair);
  R(1.4, 3.0, 2.0, 2.6, robeD);                     // 下擺
  R(2.0, 5.8, 2.6, .9, DARK);                       // 腳
  R(6.2, 3.2, 3.0, 1.0, "#8c1f18");                 // 血
  R(9.4, 4.6, 1.6, .8, "#8c1f18");

  const e = .45;
  const px = (ax, ay, aw, ah, c) => {
    g.fillStyle = c;
    g.fillRect(Math.round(ox + ax * u), Math.round(oy + ay * u),
               Math.max(1, Math.round(aw * u)), Math.max(1, Math.round(ah * u)));
  };
  ops.forEach(([ax, ay, aw, ah]) => px(ax - e, ay - e, aw + e * 2, ah + e * 2, OUTLINE));
  ops.forEach(([ax, ay, aw, ah, c]) => px(ax, ay, aw, ah, c));
}

/* ---------------------------------------------------------------- 立繪（高解析半身像）

   地圖上的小人是 16×16；立繪是 48×64，像素預算約十二倍，臉才畫得出表情。
   一樣是程式畫的，所以改個顏色就換一個人，不必外部圖檔。

   prof 除了小人那幾個欄位，另外吃：
     brow  "flat"|"stern"|"worry"|"raise"     眉毛角度＝這個人的底色
     mouth "line"|"smirk"|"small"|"frown"
     extra "scar"|null
*/
const BW = 48, BH = 64;
const SKIN_HI = "#f5d3aa", SKIN_MI = "#dcae80", SKIN_SH = "#b9855d", SKIN_DP = "#8f6242";

function drawBust(g, size, prof, o) {
  o = o || {};
  const u = size / BW, W2 = size, H2 = size * BH / BW;
  g.imageSmoothingEnabled = false;
  const R = (x, y, w, h, c) => { g.fillStyle = c;
    g.fillRect(Math.round(x*u), Math.round(y*u), Math.max(1,Math.round(w*u)), Math.max(1,Math.round(h*u))); };
  const robe = prof.robe, robeD = shade(robe, -.3), robeL = shade(robe, .14);
  const trim = prof.trim, hairC = prof.hair === "none" ? "#e8e0cd" : prof.hair;
  const hairHi = shade(hairC, .18), hairSh = shade(hairC, -.35);
  const dead = !!o.dead, wound = o.wound || 0;

  /* ---- 背：門派色的暈 + 雲紋 ---- */
  const bg = g.createLinearGradient(0, 0, 0, H2);
  bg.addColorStop(0, shade(prof.color || robe, -.55));
  bg.addColorStop(1, "#14110c");
  g.fillStyle = bg; g.fillRect(0, 0, W2, H2);
  const halo = g.createRadialGradient(W2*.5, H2*.34, 0, W2*.5, H2*.34, W2*.62);
  halo.addColorStop(0, `rgba(255,236,196,.18)`); halo.addColorStop(1, "rgba(0,0,0,0)");
  g.fillStyle = halo; g.fillRect(0, 0, W2, H2);
  for (let i = 0; i < 5; i++) {                                  // 淡淡的雲紋
    const cy = 8 + i * 11, cx = (i % 2 ? 6 : 26);
    R(cx, cy, 10, 1, "rgba(255,240,210,.05)"); R(cx + 8, cy - 2, 6, 1, "rgba(255,240,210,.05)");
  }

  /* ---- 肩與衣：斜肩，不是一塊方磚 ---- */
  R(12, 45, 24, 3, robeD); R(9, 47, 30, 3, robeD);
  R(6, 49, 36, 4, robeD);  R(3, 52, 42, 12, robeD);
  R(13, 46, 22, 3, robe);  R(10, 48, 28, 3, robe);
  R(7, 50, 34, 4, robe);   R(5, 53, 38, 11, robe);
  R(13, 46, 10, 2, robeL); R(9, 49, 8, 2, robeL);                // 左肩受光
  R(31, 49, 12, 15, robeD);                                      // 右肩暗面
  // 交領：中衣只露一條窄的 V
  R(20, 45, 8, 6, "#efe7d5"); R(21, 51, 6, 5, "#efe7d5"); R(22, 56, 4, 8, "#e4dcc6");
  R(15, 45, 6, 19, robe); R(27, 45, 6, 19, robe);
  R(18, 45, 3, 19, trim); R(27, 45, 3, 19, trim);                // 領緣
  R(19, 45, 1, 19, shade(trim, .2));
  if (prof.hat === "nun") { R(5, 52, 38, 6, "#b5762f"); R(5, 52, 38, 1, "#d09a4d");
    R(5, 57, 38, 1, "#8a5722"); }                                // 袈裟
  if (prof.acc === "jade") { R(23, 58, 3, 4, "#7fc7b8"); R(24, 55, 1, 4, "#d9c07a"); }
  else if (prof.acc === "beads") { R(10, 50, 3, 3, "#6b4a2c"); R(9, 53, 3, 3, "#8a6234");
    R(11, 56, 3, 3, "#6b4a2c"); }
  else if (prof.acc === "pouch") { R(34, 54, 7, 7, "#5f7a4a"); R(35, 55, 5, 4, "#7a9a5a");
    R(36, 52, 3, 3, "#3a342c"); }
  else if (prof.acc === "rope") { R(34, 46, 6, 4, "#8a6a3a"); R(35, 49, 5, 8, "#6b5230"); }
  else if (prof.acc === "spyglass") { R(34, 48, 4, 12, "#8a7038"); R(33, 46, 6, 4, "#c8a02e"); }
  else if (prof.acc === "whistle") { R(22, 50, 3, 2, "#cfd4da"); R(23, 52, 1, 6, "#9aa3ad"); }
  if (prof.weapon === "hammer") { R(36, 46, 4, 12, "#3a342c"); R(33, 44, 10, 5, "#6a7078");
    R(34, 45, 8, 2, "#9aa3ad"); }

  /* ---- 脖子 ---- */
  R(20, 38, 8, 9, SKIN_MI); R(20, 38, 8, 3, SKIN_SH);            // 下巴投影
  R(20, 45, 8, 2, SKIN_DP);

  /* ---- 臉：一層一層收出圓顱與尖下顎 ---- */
  R(18, 10, 12, 2, SKIN_MI);                                     // 顱頂
  R(16, 12, 16, 2, SKIN_MI);
  R(15, 14, 18, 19, SKIN_MI);                                    // 臉頰
  R(16, 33, 16, 3, SKIN_MI);                                     // 收下顎
  R(18, 36, 12, 2, SKIN_MI);
  R(20, 38, 8, 2, SKIN_MI);                                      // 下巴
  R(15, 14, 8, 19, SKIN_HI); R(18, 12, 6, 2, SKIN_HI);           // 受光的左半邊
  R(29, 15, 4, 21, SKIN_SH); R(28, 33, 4, 3, SKIN_SH);           // 右頰暗面
  R(17, 32, 14, 2, SKIN_SH);                                     // 顴骨下的影
  R(13, 21, 2, 8, SKIN_MI); R(33, 21, 2, 8, SKIN_SH);            // 耳
  R(13, 23, 1, 4, SKIN_SH);

  /* ---- 眉 ---- */
  const brow = prof.brow || "flat";
  const browY = brow === "raise" ? 20 : 21;
  if (brow === "stern") { R(17, browY + 1, 6, 2, hairSh); R(25, browY + 1, 6, 2, hairSh);
    R(17, browY + 2, 3, 2, hairSh); R(28, browY + 2, 3, 2, hairSh); }
  else if (brow === "worry") { R(17, browY + 2, 6, 2, hairSh); R(25, browY + 2, 6, 2, hairSh);
    R(20, browY + 1, 3, 2, hairSh); R(25, browY + 1, 3, 2, hairSh); }
  else { R(17, browY, 6, 2, hairSh); R(25, browY, 6, 2, hairSh); }

  /* ---- 眼 ---- */
  const eye = (ex) => {
    R(ex, 25, 7, 5, "#f6efe0");                                  // 眼白
    R(ex, 25, 7, 1, SKIN_DP);                                    // 上眼瞼
    R(ex + 2, 25, 3, 5, "#3a2b1e");                              // 虹膜
    R(ex + 3, 26, 2, 3, "#12100c");                              // 瞳
    R(ex + 3, 26, 1, 1, "#ffffff");                              // 高光
    R(ex, 29, 7, 1, SKIN_SH);
  };
  if (!dead) { eye(16); eye(25); }
  else { R(16, 27, 7, 2, SKIN_DP); R(25, 27, 7, 2, SKIN_DP); }   // 閉眼

  /* ---- 鼻與嘴 ---- */
  R(23, 27, 2, 6, SKIN_SH); R(22, 32, 4, 1, SKIN_DP); R(23, 31, 2, 1, SKIN_HI);
  const mouth = prof.mouth || "line";
  if (mouth === "smirk") { R(21, 35, 7, 1, SKIN_DP); R(27, 34, 2, 1, SKIN_DP); }
  else if (mouth === "small") { R(22, 35, 4, 1, SKIN_DP); }
  else if (mouth === "frown") { R(21, 35, 7, 1, SKIN_DP); R(20, 34, 2, 1, SKIN_DP); R(28, 34, 2, 1, SKIN_DP); }
  else { R(21, 35, 6, 1, SKIN_DP); }

  /* ---- 鬚 ---- */
  if (prof.beard === "long") {
    R(18, 36, 12, 5, "#ece6d8"); R(19, 41, 10, 7, "#e2dbcb");
    R(20, 48, 8, 7, "#ece6d8"); R(22, 55, 4, 5, "#dad3c2");      // 往下收成一綹
    R(16, 30, 3, 5, "#ece6d8"); R(29, 30, 3, 5, "#e2dbcb");      // 髭
    R(19, 36, 10, 1, "#f6f2e8");
  } else if (prof.beard === "short") {
    R(18, 36, 12, 5, hairC); R(20, 40, 8, 3, hairSh);
    R(16, 31, 4, 3, hairC); R(28, 31, 4, 3, hairC);
  } else if (prof.beard === "stub") {
    R(17, 35, 14, 5, "rgba(28,22,16,.42)"); R(16, 31, 3, 3, "rgba(28,22,16,.35)");
    R(29, 31, 3, 3, "rgba(28,22,16,.35)");
  }
  if (prof.extra === "scar") { R(30, 16, 2, 9, "#a8563f"); R(29, 18, 1, 4, "#c47a5d"); }
  if (wound >= 2) { R(15, 15, 6, 2, "#a32c22"); R(14, 17, 3, 4, "#8c1f18"); }

  /* ---- 髮與冠 ---- */
  if (prof.hat === "nun") {
    R(17, 4, 14, 3, "#f2ecdd"); R(14, 7, 20, 4, "#f2ecdd");
    R(12, 11, 24, 4, "#f2ecdd"); R(17, 4, 8, 3, "#ffffff"); R(14, 7, 9, 2, "#ffffff");
    R(11, 15, 4, 13, "#f2ecdd"); R(33, 15, 4, 13, "#e4dcc6");
    R(12, 14, 24, 2, "#d8cdb4");
    R(15, 16, 18, 2, SKIN_SH);                                   // 帽緣在額上的影
  } else {
    R(17, 5, 14, 3, hairC); R(14, 8, 20, 4, hairC);              // 髮頂順著顱形
    R(12, 12, 24, 4, hairC);
    R(17, 5, 8, 3, hairHi); R(14, 8, 9, 3, hairHi);              // 受光
    R(11, 15, 4, 17, hairC); R(33, 15, 4, 17, hairSh);           // 兩鬢
    R(14, 16, 6, 3, hairC); R(28, 16, 6, 3, hairSh);             // 額角的髮
    R(20, 16, 8, 2, hairC);                                      // 中間留額頭
    R(15, 18, 18, 1, hairSh);
    for (let i = 0; i < 5; i++) R(14 + i * 4, 9, 2, 7, hairSh);  // 髮絲
    if (prof.hat === "bun") {                                    // 髮髻＋簪
      R(19, 1, 10, 7, hairC); R(20, 1, 5, 3, hairHi); R(18, 4, 12, 2, hairSh);
      R(17, 2, 14, 1, trim); R(30, 3, 4, 1, "#d9c07a");
    } else if (prof.hat === "official") {                        // 官帽（帶翅）
      R(12, 0, 24, 9, "#241e17"); R(12, 0, 24, 2, "#3a3025");
      R(4, 4, 9, 3, "#241e17"); R(35, 4, 9, 3, "#241e17");
      R(4, 4, 9, 1, "#3a3025"); R(35, 4, 9, 1, "#3a3025");
      R(22, 0, 4, 9, "#c8a02e"); R(23, 2, 2, 5, "#e8c766");
      R(12, 8, 24, 2, "#12100c");
    } else if (prof.hat === "band") {                            // 抹額
      R(12, 12, 24, 4, "#c8a02e"); R(12, 15, 24, 1, "#8c6a15");
      R(12, 12, 24, 1, "#e8c766"); R(34, 13, 6, 8, "#c8a02e");
    }
  }

  /* ---- 隨身：酒葫蘆掛在肩邊 ---- */
  if (prof.acc === "gourd") {
    R(36, 50, 9, 11, "#c08a3e"); R(38, 45, 5, 6, "#c08a3e"); R(37, 51, 3, 6, "#d9a95e");
    R(39, 42, 3, 3, "#6b4a2c"); R(36, 49, 9, 1, "#8a5f28");
  }

  /* ---- 邊框：像掛軸 ---- */
  R(0, 0, BW, 1, "#0f0d09"); R(0, BH-1, BW, 1, "#0f0d09");
  R(0, 0, 1, BH, "#0f0d09"); R(BW-1, 0, 1, BH, "#0f0d09");
  R(1, 1, BW-2, 1, "rgba(217,166,60,.35)"); R(1, BH-2, BW-2, 1, "rgba(217,166,60,.25)");
  R(1, 1, 1, BH-2, "rgba(217,166,60,.3)"); R(BW-2, 1, 1, BH-2, "rgba(217,166,60,.2)");
  if (dead) {                                                    // 已歿：整張壓灰壓暗
    g.fillStyle = "rgba(20,16,12,.52)"; g.fillRect(0, 0, W2, H2);
  }
}

function drawPortrait(g, size, prof, o) {
  g.imageSmoothingEnabled = false;
  g.clearRect(0, 0, size, size);
  drawFigure(g, size/2, size*.97, size*.94, prof, Object.assign({ face:"down", frame:0 }, o || {}));
}
