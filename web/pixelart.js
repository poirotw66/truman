/* 共用像素美術：地圖圖磚 + 人物畫法。
   回放頁（replay/template.html）與人物工作室（cast/editor.template.html）都注入這一支，
   兩邊看起來才會是同一個世界。純函式、無 DOM 依賴（除了 canvas）。

   對外：
     ART                              一格 = 16 個美術像素
     buildWorldCanvas(rows, areas, TS, decor?)   → 畫好的離屏 canvas
     DECOR_JIANGHU                    衡山城的固定裝飾（樹/井/紅氈/幌子）
     drawFigure(g, cx, footY, T, prof, opts)     人物（腳底站在 footY）
     drawPortrait(g, size, prof, opts)           方形頭像
   prof 欄位：robe trim hair hat(bun|official|nun|band|none) beard(long|short|stub|none)
              weapon(sword|blade|qin|null)
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
  plaza:["#9fa093","#96978a","#a8a99b"],
  dirt:["#a08a5f","#977f55"],
  gate:"#6b6252",
};
/* 室內是「掀了屋頂」的畫法（人在屋裡就看得見人），牆畫在區塊邊緣，南面留門。 */
const INT = {
  l:{floor:"#b58f57", plank:"#9c7743", wall:"#7c4331", wallHi:"#a35a3c", kind:"wood"},
  t:{floor:"#a87c46", plank:"#8d6434", wall:"#6b4425", wallHi:"#8b5c33", kind:"wood"},
  p:{floor:"#a8767c", plank:"#8f5e66", wall:"#7d3f47", wallHi:"#a2545e", kind:"wood"},
  c:{floor:"#8e8b7c", plank:"#7c7969", wall:"#5b5850", wallHi:"#767264", kind:"stone"},
  z:{floor:"#7b7566", plank:"#6a6456", wall:"#4c473b", wallHi:"#615c4c", kind:"stone", ruin:true},
};
const DECOR_JIANGHU = [
  {t:"carpet", x:2, y:4}, {t:"carpet", x:3, y:4}, {t:"carpet", x:4, y:4}, {t:"carpet", x:4, y:5},
  {t:"tree", x:6, y:4}, {t:"tree", x:19, y:4}, {t:"tree", x:6, y:13}, {t:"tree", x:17, y:12}, {t:"tree", x:14, y:8},
  {t:"well", x:8, y:12}, {t:"crate", x:16, y:5}, {t:"crate", x:9, y:13}, {t:"banner", x:17, y:4},
];

function buildWorldCanvas(rows, areas, TS, decor) {
  const W = rows[0].length, H = rows.length;
  const cv = document.createElement("canvas");
  cv.width = W * TS; cv.height = H * TS;
  const wx = cv.getContext("2d");
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
        if (h(7) > .55 && !N) { R(2,5,12,4,"#8a4f57"); R(2,5,12,1,"#c2848c"); }
        if (h(8) > .8) R(5,10,6,4,"#6d3a41");
      } else if (sym === "c") {                                       // 廟：蒲團
        if (h(6) > .6 && !N) { R(5,8,6,4,"#9a8a5c"); R(5,8,6,1,"#b7a675"); }
      }

      // 牆
      if (N) {
        R(0,0,16,5,p.wall); R(0,4,16,1,p.wallHi);
        if (sym === "l" && x === doorX) { R(3,0,10,4,"#8c2b22"); R(3,0,10,1,"#d9a63c"); R(3,3,10,1,"#d9a63c"); R(6,1,4,2,"#d9a63c"); }
        else if (sym === "t") { R(1,0,14,4,"#5d3a1c"); for (let rx=2; rx<14; rx+=3) { R(rx,1,2,3,"#8a6a3c"); R(rx,1,2,1,"#c8a02e"); } }
        else if (sym === "p") { for (let rx=0; rx<16; rx+=3) R(rx,0,2,5,"#914b55"); R(0,4,16,1,"#d9a63c"); }
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
      R(0,0,16,1,"#87887c"); R(0,0,1,16,"#87887c");
      if (h(7) > .8) R(4,5,7,4,"#8b8c80");
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
    // 長街
    R(0,0,16,16,PAL.street[Math.floor(h(1)*3)]);
    for (let i=0; i<4; i++) { const cx = Math.floor(h(10+i)*12)+1, cy = Math.floor(h(20+i)*12)+1;
      if (hash(x,y,30+i) > .45) R(cx,cy,3,2,PAL.streetLine); }
    if (h(6) > .93) { R(6,7,4,3,"#6e7a45"); R(7,6,2,2,"#7f8c4e"); }
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

/* ---------------------------------------------------------------- 人物 */
const SKIN = "#e4b98d", SKIN_D = "#c99a6f", DARK = "#1b1610";

function drawFigure(g, cx, footY, T, prof, o) {
  o = o || {};
  const u = T / ART, face = o.face || "down", fr = o.frame || 0, hurt = o.wound || 0;
  const bob = o.bob || 0;
  const ox = cx - 8*u, oy = footY - 15*u + bob*u;
  const R = (ax, ay, aw, ah, c) => { g.fillStyle = c; g.fillRect(Math.round(ox+ax*u), Math.round(oy+ay*u), Math.max(1,Math.round(aw*u)), Math.max(1,Math.round(ah*u))); };
  const flip = face === "left";
  const FX = (ax, aw) => flip ? 16 - ax - aw : ax;
  const RX = (ax, ay, aw, ah, c) => R(FX(ax,aw), ay, aw, ah, c);
  const side = face === "left" || face === "right";

  g.fillStyle = "rgba(0,0,0,.3)";
  g.beginPath(); g.ellipse(cx, footY - u, 5.2*u, 2*u, 0, 0, 6.3); g.fill();

  if (o.dead) { drawFallen(g, cx, footY, T, prof); return; }

  const swing = (fr === 1 || fr === 3) ? 1 : 0, back = fr === 3 ? 1 : 0;
  // 腿
  R(5.5, 12 - swing, 2, 3 + swing, prof.trim); R(8.5, 12 - back, 2, 3 + back, prof.trim);
  R(5.3, 14.6, 2.4, 1.2, DARK); R(8.3, 14.6, 2.4, 1.2, DARK);
  // 身
  const bw = side ? 6 : 8, bodyX = side ? 5 : 4;
  R(bodyX, 6.4, bw, 6.4, prof.robe);
  R(bodyX, 12.2, bw, 1.1, prof.trim);
  R(bodyX, 9.4, bw, 1.1, prof.trim);
  R(bodyX+1, 9.4, bw-2, 1.1, prof.hat === "official" ? "#c8a02e" : prof.trim);
  // 袖
  if (side) { RX(10.6, 7, 2, 4.6, prof.robe); RX(11, 11.2, 1.6, 1.4, SKIN); }
  else { R(3, 7, 1.8, 4.6, prof.robe); R(11.2, 7, 1.8, 4.6, prof.robe);
         R(3.1, 11.2, 1.6, 1.4, SKIN); R(11.3, 11.2, 1.6, 1.4, SKIN); }
  // 頭
  R(5.4, 2.2, 5.2, 5, SKIN); R(5.4, 6.6, 5.2, .8, SKIN_D);
  if (face === "up") { R(5.4, 2.2, 5.2, 4, prof.hair === "none" ? "#efe7d5" : prof.hair); }
  else {
    if (prof.hat === "nun") { R(4.9, 1.7, 6.2, 2.6, "#efe7d5"); R(4.9, 3.9, 1, 2.6, "#efe7d5"); R(10.1, 3.9, 1, 2.6, "#efe7d5"); }
    else { R(5.1, 1.6, 5.8, 2.2, prof.hair); R(5.1, 3.2, 1, 2.4, prof.hair); R(9.9, 3.2, 1, 2.4, prof.hair); }
    if (prof.hat === "bun") { R(7.2, .2, 2.4, 1.8, prof.hair); R(7.4, -.3, 2, .8, prof.trim); }
    if (prof.hat === "official") { R(4.4, .3, 7.2, 2.2, "#221d16"); R(3, 1, 1.6, 1.2, "#221d16"); R(11.4, 1, 1.6, 1.2, "#221d16"); R(7.4, .3, 1.2, 2.2, "#c8a02e"); }
    if (prof.hat === "band") { R(5, 2.4, 6, 1, "#c8a02e"); }
    // 眼
    if (face === "down") { R(6.3, 4.3, 1, 1.1, DARK); R(8.8, 4.3, 1, 1.1, DARK); }
    else if (side) { RX(8.6, 4.3, 1, 1.1, DARK); }
    // 鬚
    if (prof.beard === "long") { R(6.6, 6.6, 3, 2.6, "#e6e0d2"); R(6.2, 5.4, .9, 1.4, "#e6e0d2"); R(9, 5.4, .9, 1.4, "#e6e0d2"); }
    else if (prof.beard === "short") { R(6.8, 6.4, 2.6, 1.2, prof.hair); }
    else if (prof.beard === "stub") { R(6.4, 6.2, 3.4, 1.1, "rgba(30,24,16,.55)"); }
  }
  // 兵器
  if (prof.weapon === "sword") { RX(11.4, 3.4, 1.1, 8, "#8a6a3c"); RX(11.2, 2.6, 1.6, 1.2, "#d9c07a"); }
  else if (prof.weapon === "blade") { RX(11.2, 3.6, 1.4, 7.4, "#4a4a52"); RX(11, 3.2, 2, 1, "#cfd4da"); RX(11.3, 10.6, 1.2, 1.6, "#8a3a2f"); }
  else if (prof.weapon === "qin") { RX(2.2, 8, 3, 5.4, "#6b4a2c"); RX(2.6, 8.4, .5, 4.6, "#d9c07a"); RX(3.6, 8.4, .5, 4.6, "#d9c07a"); }
  // 傷
  if (hurt >= 1) R(6.4, 8, 2.2, 1.4, "#a32c22");
  if (hurt >= 2) { R(5, 7, 1.6, 3.4, "#8c1f18"); R(9.4, 10.2, 2.4, 1.6, "#8c1f18"); R(6.2, 3.6, 1.4, .9, "#a32c22"); }
}

function drawFallen(g, cx, footY, T, prof) {
  const u = T / ART, oy = footY - 6*u, ox = cx - 8*u;
  const R = (ax, ay, aw, ah, c) => { g.fillStyle = c; g.fillRect(Math.round(ox+ax*u), Math.round(oy+ay*u), Math.max(1,Math.round(aw*u)), Math.max(1,Math.round(ah*u))); };
  g.fillStyle = "rgba(120,20,14,.5)";
  g.beginPath(); g.ellipse(cx, footY - 1.5*u, 6.6*u, 2.8*u, 0, 0, 6.3); g.fill();
  R(3.4, 2.2, 8, 3.4, prof.robe); R(3.4, 4.2, 8, 1, prof.trim);
  R(11.2, 2, 3.2, 3.4, SKIN);
  R(12.4, 1.6, 2.4, 2, prof.hat === "nun" ? "#efe7d5" : (prof.hair === "none" ? "#efe7d5" : prof.hair));
  R(2, 2.6, 2, 2.6, prof.trim);
  R(5, 3, 3.4, 1.2, "#8c1f18");
}

function drawPortrait(g, size, prof, o) {
  g.imageSmoothingEnabled = false;
  g.clearRect(0, 0, size, size);
  drawFigure(g, size/2, size*.97, size*.94, prof, Object.assign({ face:"down", frame:0 }, o || {}));
}
