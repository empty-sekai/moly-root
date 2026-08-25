// fx/particle-shader.js — 粒子的片元链(一份着色器,三种绘制模式共用)
//
// 在这个文件之前,一颗粒子画的是「一张基础贴图乘顶点色」:每颗粒子一个内建材质,
// 材质上挂一张贴图、一个颜色、一个不透明度。那不是这一族着色器做的事 —— 它做的是
// 一条有次序的片元链,链上每一环都由材质自己的开关决定开不开。少了其中任何一环,
// 画面上不会报错,只会画错;最刺眼的一处是**过渡图那一环**:基础图的缩放偏移常常
// 把取样框裁到圆盘中间那块纯白上,轮廓根本不在基础图里,而在过渡图里。少读一张
// 过渡图,一颗柔和的圆点就变成一块白方片。
//
// ---- 这条链的次序(前向 pass;发光是另一条 pass,本文件不做) --------------------
//
//   1. 逐粒子取值表:两个自定义向量(下称 custom1 / custom2)。
//   2. 边缘量  rim = 1 - |dot(N, V)|,N 是世界法线、V 是视线方向。
//   3. flow 扰动:算一次位移向量,加到被选中的每一条 uv 上。
//   4. 基础采样 B = 采(基础图, 基础 uv)。
//   5. 染色(乘):乘数 = 1 + 混合率 * (染色源 - 1),**vec4 逐通道乘,alpha 也乘**。
//   6. 假光(对 RGB 加一个插值项)。
//   7. **alpha 过渡(乘)**:溶解式或淡出式,二选一或都不做。
//   8. 顶点色(乘):RGB 与 alpha 同时。
//   9. 边缘透明度(乘 alpha)。
//  10. 亮度透明度(乘 alpha,运行期分支)。
//  11. 现象光(对 RGB 做插值) —— 本文件**未做**,见下。
//  12. 软粒子(乘 alpha) —— 本文件**未做**,见下。
//
// alpha 是一条纯乘法链:base.a × 染色.a × 过渡 × 顶点色.w × 边缘 × 亮度 × 软粒子。
//
// ---- 逐粒子选择器 ------------------------------------------------------------
//
// 材质里一大批叫 `*Coord` 的字段是**打包的选择器**,不是数值:个位选哪一个向量
// (0 = 零流、1 = custom1、2 = custom2),十位选哪一个分量(0..3 = x/y/z/w)。
// 选出来的逐粒子值与**同名的材质常量**之间是**加法**,不是乘法。
//
// ---- 寻址:一张图上到底取哪一块 ----------------------------------------------
//
// 每一槽的 uv 都是从同一条网格 uv0 出发的,各自套自己的缩放偏移,再加自己的逐粒子
// 偏移。**只有基础槽有旋转**,而且旋转在缩放偏移**之前**:
//
//   基础  uvRot  = 绕轴心 p 旋转 turns 圈的 uv0,轴心 p = vec2(0.5) - _BaseMapRotationOffsets.xy
//         turns  = sel(_BaseMapRotationCoord) + _BaseMapRotation      // 单位是「圈」
//         uvBase = uvRot * _BaseMap_ST.xy + _BaseMap_ST.zw
//                  + vec2( sel(_BaseMapOffsetXCoord), sel(_BaseMapOffsetYCoord) )
//   过渡  uvTrans = uv0 * _AlphaTransitionMap_ST + 两个逐粒子偏移      // 无旋转
//   flow  uvFlow  = uv0 * _FlowMap_ST            + 两个逐粒子偏移      // 无旋转
//   染色  uvTint  = uv0 * _TintMap_ST                                 // 律里就没有偏移
//
// 片数(`_BaseMapSliceCount`)**只属于数组档**:二维档的算式里一次都不引用它,
// 二维档的「取哪一片」全靠上面那个窗口加逐粒子偏移。数组档的片索引是
//
//   slice = fract(clamp(sel(_BaseMapProgressCoord) + _BaseMapProgress, 0, 0.999000013))
//           * _BaseMapSliceCount - 0.5
//
// 这一段在顶点段,顶点段每帧都跑 —— 所以片索引是**逐帧**量。它换的是整张贴图对象,
// 不是这条片元链上的量,所以由发射器一侧做(见 emoticon.js 的 `_arrayLayerIndex`)。
//
// 自发光槽也有一整套同样的寻址(两个逐粒子偏移、片数、进度),而整条发光链是另一个
// pass,本文件不做 —— 于是这一槽的寻址**一项都没读**,单独记一笔(`addrEmissionUnread`)。
//
// ---- 本文件明确没有做的几段(各有独立计数器,见 `counts`) ----------------------
//
//   * 软粒子:要一张场景深度图,这条管线没有;
//   * 发光:是另一个 pass 的输出,不进前向这条颜色链;
//   * flow 对染色目标 / 发光目标的扰动:这两条目标的消费点都在本文件不做的那几段里;
//   * 顶点形变:改的是顶点位置,不是片元;
//   * 现象光:要一个由环境下发的全局光色,**这个量未取得**;
//   * 高动态染色:见下面「高动态」一节。
//
// 另有四组材质字段是**声明了但引擎一个变体都没编译**的(过渡图的数组/三维取样、
// 发光色渐变图、保留边缘透明、深度淡出)。它们不是「我们没做」,是原版就没有实现,
// 所以单列一类计数,不许混进「未做」里。
//
// ---- 高动态染色 ---------------------------------------------------------------
//
// 有些染色乘数是高动态的(通道可以到几十),原版靠色调映射把它收回可显示范围。
// 这条管线是刻意的 gamma 直通、没有色调映射,硬乘只会在帧缓冲里削平成纯白方片 ——
// 比不乘更远离原版,而且看着像有东西。所以乘数任一通道大于 1 时**不画染色**并计数。
// 缺的是色调映射那条律,记在它头上,不在这里塞补偿系数。

// 未绑贴图的槽位要有东西可采(取样器不能悬空)。1×1 全白,配合各自的开关使用 ——
// 开关关掉时它根本不进算式,只是让程序能链接。
let WHITE = null;
function whiteTexture(THREE) {
  if (!WHITE) {
    WHITE = new THREE.DataTexture(new Uint8Array([255, 255, 255, 255]), 1, 1);
    WHITE.needsUpdate = true;
  }
  return WHITE;
}

const VERTEX_COMMON = `
varying vec2 vParticleUv;
varying vec3 vParticleWorld;
varying vec3 vParticleNormal;
`;

// 朝相机的四边形。位置在视空间里绕视轴旋转后平移,与内建的精灵装配同构;
// 保留 gl_Position 那一行的原文,深度偏移那条改写要按它定位。
//
// 法线:这个模式的四边形永远正对相机,所以它的法线就是视线方向 —— 这一条是由
// 「朝相机」这个模式定义直接给出的,不是另读的。于是这一档的 rim 恒为 0。
const VERTEX_BILLBOARD = `
uniform float uRotation;
${VERTEX_COMMON}
void main() {
  vParticleUv = uv;
  vec4 mvPosition = modelViewMatrix * vec4( 0.0, 0.0, 0.0, 1.0 );
  vec2 scale = vec2( length( modelMatrix[ 0 ].xyz ), length( modelMatrix[ 1 ].xyz ) );
  vec2 aligned = position.xy * scale;
  float c = cos( uRotation );
  float s = sin( uRotation );
  mvPosition.xy += vec2( c * aligned.x - s * aligned.y, s * aligned.x + c * aligned.y );
  vParticleWorld = ( modelMatrix * vec4( 0.0, 0.0, 0.0, 1.0 ) ).xyz;
  vParticleNormal = vec3( 0.0 );          // 空 = 用视线方向当法线(见上)
  gl_Position = projectionMatrix * mvPosition;
}
`;

// 有真实朝向的几何体(贴地的四边形、网格件)。世界法线由视空间法线反解:
// 视矩阵的旋转部分是正交的,右乘等于乘它的转置,也就是它的逆。
const VERTEX_SHAPED = `
${VERTEX_COMMON}
void main() {
  vParticleUv = uv;
  vec3 viewNormal = normalMatrix * normal;
  vParticleNormal = ( vec4( viewNormal, 0.0 ) * viewMatrix ).xyz;
  vParticleWorld = ( modelMatrix * vec4( position, 1.0 ) ).xyz;
  vec4 mvPosition = modelViewMatrix * vec4( position, 1.0 );
  gl_Position = projectionMatrix * mvPosition;
}
`;

const FRAGMENT = `
precision highp float;

// —— 逐粒子(每次绘制前写入) ——
uniform vec3  uColor;          // 顶点色 RGB(出生色 × 生命期色)
uniform float uAlpha;          // 顶点色 alpha
uniform vec4  uCustom1;        // 逐粒子向量 1
uniform vec4  uCustom2;        // 逐粒子向量 2
uniform vec4  uSheet;          // xy = 1/格数, zw = 这一帧的格偏移

// —— 基础图 ——
uniform sampler2D uBaseMap;
uniform vec4  uBaseST;         // xy 缩放, zw 偏移
uniform float uBaseRotEnabled;
uniform float uBaseRotation;
uniform float uBaseRotCoord;
uniform vec2  uBaseRotPivot;   // 旋转轴心的偏移量
uniform vec2  uBaseOffsetCoord;

// —— 染色 ——
uniform float uTintMode;       // 0 不做, 1 全域, 2 边缘
uniform float uTintUseMap;     // 1 = 用染色贴图(与材质染色互斥)
uniform sampler2D uTintMap;
uniform vec4  uTintMapST;
uniform vec4  uTintColor;
uniform float uTintBlendRate;
uniform float uTintBlendRateCoord;
uniform float uTintRimProgress;
uniform float uTintRimProgressCoord;
uniform float uTintRimSharpness;
uniform float uTintRimSharpnessCoord;
uniform float uInverseTintRim;

// —— 假光 ——
uniform float uFakeLightEnabled;
uniform vec3  uFakeLightDirection;
uniform vec4  uFakeLightColor;

// —— alpha 过渡 ——
uniform float uTransMode;      // 0 不做, 1 溶解, 2 淡出
uniform sampler2D uTransMap;
uniform vec4  uTransST;
uniform vec2  uTransOffsetCoord;
uniform float uTransChannel;
uniform float uTransProgress;
uniform float uTransProgressCoord;
uniform float uDissolveSharpness;
uniform float uVertexAlphaAsProgress;

// —— flow 扰动 ——
uniform float uFlowToBase;
uniform float uFlowToTrans;
uniform sampler2D uFlowMap;
uniform vec4  uFlowST;
uniform vec2  uFlowOffsetCoord;
uniform float uFlowIntensity;
uniform float uFlowIntensityCoord;
uniform float uFlowChannelX;
uniform float uFlowChannelY;

// —— 边缘透明度 ——
uniform float uRimTransparency;
uniform float uRimProgress;
uniform float uRimProgressCoord;
uniform float uRimSharpness;
uniform float uRimSharpnessCoord;
uniform float uInverseRimTransparency;

// —— 亮度透明度 ——
uniform float uLuminance;
uniform float uLumProgress;
uniform float uLumProgressCoord;
uniform float uLumSharpness;
uniform float uLumSharpnessCoord;
uniform float uInverseLuminance;

${VERTEX_COMMON}

/** 打包选择器取值:个位选向量(0 零流 / 1 / 2),十位选分量。 */
float molySel( float coord ) {
  int c = int( max( coord, 0.0 ) + 0.5 );
  int which = c - ( c / 10 ) * 10;
  int comp = c / 10;
  vec4 v = vec4( 0.0 );
  if ( which == 1 ) v = uCustom1;
  else if ( which == 2 ) v = uCustom2;
  if ( comp == 1 ) return v.y;
  if ( comp == 2 ) return v.z;
  if ( comp == 3 ) return v.w;
  return v.x;
}

/** 直接的通道号(0..3 = x/y/z/w),与上面的打包选择器不是一回事。 */
float molyChannel( vec4 v, float channel ) {
  int i = int( max( channel, 0.0 ) + 0.5 );
  if ( i == 1 ) return v.y;
  if ( i == 2 ) return v.z;
  if ( i == 3 ) return v.w;
  return v.x;
}

/**
 * 进度/锐度这一对参数共用的阈值带 + smoothstep。
 * 下界 = P*(2-S) - (1-S),带宽 = 1-S。
 *
 * **未取得**:S 取到 1 时带宽为 0,原版在这里是一个除零,着色语言未定义,
 * 也没有读出引擎侧有没有守卫。这里取它的极限形状 —— 在下界处硬阶跃。
 */
float molyBand( float x, float progress, float sharpness ) {
  float P = min( progress, 1.0 );
  float S = min( sharpness, 1.0 );
  float lo = P * ( 2.0 - S ) - ( 1.0 - S );
  float den = 1.0 - S;
  float t = den > 1e-6 ? clamp( ( x - lo ) / den, 0.0, 1.0 ) : ( x >= lo ? 1.0 : 0.0 );
  return t * t * ( 3.0 - 2.0 * t );
}

void main() {
  // 片表动画改写的是这颗粒子的 uv0;后面每一张图的 uv 都从它出发。
  vec2 uv0 = vParticleUv * uSheet.xy + uSheet.zw;

  vec3 toCamera = cameraPosition - vParticleWorld;
  float toCameraLen = length( toCamera );
  vec3 V = toCameraLen > 1e-6 ? toCamera / toCameraLen : vec3( 0.0, 0.0, 1.0 );
  vec3 N = dot( vParticleNormal, vParticleNormal ) > 1e-8 ? normalize( vParticleNormal ) : V;
  float facing = abs( dot( N, V ) );
  float rim = 1.0 - facing;

  // 基础 uv:先(可选)绕轴心旋转,再套这一槽自己的缩放偏移,最后加两个逐粒子偏移。
  vec2 uvRot = uv0;
  if ( uBaseRotEnabled > 0.5 ) {
    float turns = molySel( uBaseRotCoord ) + uBaseRotation;
    float th = 6.28318548 * turns;
    float cs = cos( th );
    float sn = sin( th );
    vec2 pivot = vec2( 0.5 ) - uBaseRotPivot;
    vec2 d = uv0 - pivot;
    uvRot = pivot + vec2( dot( d, vec2( cs, sn ) ), dot( d, vec2( -sn, cs ) ) );
  }
  vec2 uvBase = uvRot * uBaseST.xy + uBaseST.zw;
  uvBase += vec2( molySel( uBaseOffsetCoord.x ), molySel( uBaseOffsetCoord.y ) );

  vec2 uvTrans = uv0 * uTransST.xy + uTransST.zw;
  uvTrans += vec2( molySel( uTransOffsetCoord.x ), molySel( uTransOffsetCoord.y ) );

  vec2 uvTint = uv0 * uTintMapST.xy + uTintMapST.zw;   // 这一槽没有逐粒子偏移

  // flow:位移向量只算一次,加到被选中的每一条 uv 上;flow 图自己的 uv 不被扰动。
  if ( uFlowToBase > 0.5 || uFlowToTrans > 0.5 ) {
    vec2 uvFlow = uv0 * uFlowST.xy + uFlowST.zw;
    uvFlow += vec2( molySel( uFlowOffsetCoord.x ), molySel( uFlowOffsetCoord.y ) );
    float amount = molySel( uFlowIntensityCoord ) + uFlowIntensity;
    vec4 F = texture2D( uFlowMap, uvFlow );
    vec2 flow = vec2( molyChannel( F, uFlowChannelX ), molyChannel( F, uFlowChannelY ) ) * 2.0 - 1.0;
    if ( uFlowToBase > 0.5 ) uvBase += flow * amount;
    if ( uFlowToTrans > 0.5 ) uvTrans += flow * amount;
  }

  vec4 B = texture2D( uBaseMap, uvBase );

  // 染色。全域档的混合率是一个常量,边缘档还要按边缘权重再压一次,并且**覆写** rim ——
  // 后面的边缘透明度消费的就是覆写后的那个量。
  vec4 tintMult = vec4( 1.0 );
  if ( uTintMode > 0.5 ) {
    float rate = molySel( uTintBlendRateCoord ) + uTintBlendRate;
    float blend;
    if ( uTintMode > 1.5 ) {
      float rimSel = uInverseTintRim >= 0.5 ? facing : ( 1.0 - facing );
      float area = molyBand( rimSel,
                             molySel( uTintRimProgressCoord ) + uTintRimProgress,
                             molySel( uTintRimSharpnessCoord ) + uTintRimSharpness );
      // 这一档里染色混合率出现了平方项(带权之后又乘了一次材质常量),照实记。
      blend = clamp( area * uTintBlendRate * rate, 0.0, 1.0 );
      rim = area;
    } else {
      blend = clamp( rate, 0.0, 1.0 );
    }
    vec4 src = uTintUseMap > 0.5 ? texture2D( uTintMap, uvTint ) : uTintColor;
    tintMult = vec4( 1.0 ) + blend * ( src - vec4( 1.0 ) );
  }
  vec4 C = B * tintMult;

  // 假光:从「已染色的颜色」朝一个固定光色插值,只动 RGB。
  if ( uFakeLightEnabled > 0.5 ) {
    float d = min( abs( dot( N, uFakeLightDirection ) ), 1.0 ) * uFakeLightColor.w;
    C.rgb = C.rgb + d * ( uFakeLightColor.rgb - B.rgb * tintMult.rgb );
  }

  // alpha 过渡。两种模式互斥,都是拿过渡图的一个通道与一个进度量比出一个 0..1 的因子。
  if ( uTransMode > 0.5 ) {
    float q = molySel( uTransProgressCoord ) + uTransProgress;
    if ( uVertexAlphaAsProgress > 0.5 ) q += 1.0 - uAlpha;
    q = min( q, 1.0 );
    float s = molyChannel( texture2D( uTransMap, uvTrans ), uTransChannel );
    float t;
    if ( uTransMode > 1.5 ) {
      t = clamp( s - ( 2.0 * q - 1.0 ), 0.0, 1.0 );          // 淡出:没有 smoothstep
    } else {
      float a = 0.5 - 0.499900013 * uDissolveSharpness;      // 溶解
      float b = 2.0 * a + 1.0;
      float lo = b * q - 2.0 * a;
      t = clamp( ( s - lo ) / ( 2.0 * a ), 0.0, 1.0 );
      t = t * t * ( 3.0 - 2.0 * t );
    }
    C.a *= t;
  }

  // 顶点色。带「顶点 alpha 当过渡进度」的那一档只乘 RGB,顶点 alpha 不再进输出 alpha。
  C.rgb *= uColor;
  if ( uVertexAlphaAsProgress < 0.5 ) C.a *= uAlpha;

  if ( uRimTransparency > 0.5 ) {
    float rimT = uInverseRimTransparency >= 0.5 ? rim : ( 1.0 - rim );
    C.a *= molyBand( rimT,
                     molySel( uRimProgressCoord ) + uRimProgress,
                     molySel( uRimSharpnessCoord ) + uRimSharpness );
  }

  if ( uLuminance > 0.5 ) {
    float luma = dot( C.rgb, vec3( 0.298911989, 0.586610973, 0.114478 ) );
    float x = uInverseLuminance >= 0.5 ? ( 1.0 - luma ) : luma;
    C.a *= molyBand( x,
                     molySel( uLumProgressCoord ) + uLumProgress,
                     molySel( uLumSharpnessCoord ) + uLumSharpness );
  }

  gl_FragColor = C;
}
`;

const n = (v, d = 0) => (Number.isFinite(+v) ? +v : d);

function st4(record, key) {
  const a = record && record.textureScaleOffset && record.textureScaleOffset[key];
  return Array.isArray(a) ? [n(a[0], 1), n(a[1], 1), n(a[2], 0), n(a[3], 0)] : [1, 1, 0, 0];
}

/**
 * 一个 `*Coord` 打包选择器指向哪个逐粒子向量的哪个分量,或者 null(零流)。
 * 个位选向量、十位选分量 —— 反过来读会整条绑错。
 */
export function coordSource(coord) {
  const value = Math.round(n(coord, 0));
  const which = value % 10;
  if (which !== 1 && which !== 2) return null;
  return { vector: which === 1 ? 'custom1' : 'custom2',
           component: 'xyzw'[Math.floor(value / 10)] || 'x' };
}

/**
 * 一个发射器的着色件。
 *
 * 一个发射器 = 一份材质对象,**不是**一颗粒子一份:这条链上除了颜色、alpha、旋转、
 * 贴图帧与两个逐粒子向量以外,全部取值都是材质常量,逐粒子那几个每次绘制前写进
 * uniform 就够了。
 *
 * @param THREE      渲染库
 * @param ctx        `{ record, state, baseST, mode, applyZOffset }`
 *                   `record` 是解码出来的材质记录;`state` 是已经从着色器族解出来的
 *                   混合/深度状态;`baseST` 是**实际在采的那一槽**的缩放偏移。
 */
export function createParticleShading(THREE, ctx) {
  const record = (ctx.record && !ctx.record.external) ? ctx.record : null;
  const floats = (record && record.floats) || {};
  const colors = (record && record.colors) || {};
  const keywords = new Set((record && record.keywords) || []);
  const textures = (record && record.textures) || {};
  const white = whiteTexture(THREE);
  const st = ctx.state;

  // 「声明了而本文件没做」与「原版就没编译过」是两回事,分开数。
  const counts = {
    softParticles: 0, emission: 0, emissionMap: 0,
    flowToTint: 0, flowToEmission: 0, vertexDeformation: 0,
    phenomenaLight: 0, alphaModulate: 0, baseMirrorSampler: 0,
    // 属性在材质里,但原版一个已发货变体都没有编译过它
    unshippedEmissionColorRamp: 0, unshippedKeepEdge: 0, unshippedDepthFade: 0,
    unshippedTransitionArray: 0,
    // 做到了的那几段
    tintApplied: 0, tintRefused: 0, tintHdrUnrepresented: 0,
    transitionDissolve: 0, transitionFade: 0, transitionNoMap: 0,
    rimTransparency: 0, luminance: 0, fakeLight: 0,
    flowToBase: 0, flowToTransition: 0,
    // —— 逐槽的寻址账(见下面「寻址」一节) ——
    // 做到了的
    addrBaseOffset: 0, addrBaseRotation: 0, addrTransitionOffset: 0, addrFlowOffset: 0,
    // 声明了而本文件没读的
    addrEmissionUnread: 0, addrVertexDeformUnread: 0,
    // 原版一个变体都没编译过的寻址项
    addrUnshippedTransitionSlice: 0, addrUnshippedTintSlice: 0, addrUnshippedParallax: 0,
  };

  if (n(floats._SoftParticlesEnabled, 0) > 0.5 || keywords.has('_SOFT_PARTICLES_ENABLED')) {
    counts.softParticles = 1;
  }
  if (keywords.has('_EMISSION_AREA_ALL') || keywords.has('_EMISSION_AREA_MAP')) counts.emission = 1;
  if (keywords.has('_EMISSION_AREA_MAP')) counts.emissionMap = 1;
  if (keywords.has('_FLOW_MAP_TARGET_TINT')) counts.flowToTint = 1;
  if (keywords.has('_FLOW_MAP_TARGET_EMISSION')) counts.flowToEmission = 1;
  if (keywords.has('_VERTEX_DEFORMATION_ENABLED')) counts.vertexDeformation = 1;
  if (n(floats._PhenomenaLightEnabled, 0) > 0.5) counts.phenomenaLight = 1;
  if (keywords.has('_ALPHAMODULATE_ENABLED')) counts.alphaModulate = 1;
  if (keywords.has('_BASE_SAMPLER_STATE_LINEAR_MIRROR')) counts.baseMirrorSampler = 1;
  if (textures._EmissionColorRamp) counts.unshippedEmissionColorRamp = 1;
  if (n(floats._KeepEdgeTransparency, 0) > 0.5) counts.unshippedKeepEdge = 1;
  if (n(floats._DepthFadeEnabled, 0) > 0.5) counts.unshippedDepthFade = 1;
  if (textures._AlphaTransitionMap2DArray || textures._AlphaTransitionMap3D) {
    counts.unshippedTransitionArray = 1;
  }

  // —— 逐槽的寻址账 ——
  //
  // 「寻址」= 一张图上到底取哪一块:那一槽自己的缩放偏移、两个逐粒子偏移、
  // (只有基础槽有的)旋转,数组档再加一个片索引。
  //
  // 一个 `*Coord` 只有在个位是 1 或 2 时才真的动手(0 是零流,加的是 0)。所以
  // 「材质里写着这个字段」不等于「这个字段起作用」—— 片数默认一律是 4,拿「声明了」
  // 当口径会让每个材质都中一次,数出来的全是噪声。下面一律按**会不会动手**记:
  // 选择器指到真流、或进度量非零、或数组/三维那一槽真的绑着图。
  //
  // 三类分开记,不许合并:做到了的、声明了而本文件没读的、原版一个变体都没编译过的。
  const acts = (key) => !!coordSource(floats[key]);
  const nz = (key) => Math.abs(n(floats[key], 0)) > 1e-6;

  if (acts('_BaseMapOffsetXCoord') || acts('_BaseMapOffsetYCoord')) counts.addrBaseOffset = 1;
  if (n(floats._BaseMapRotationEnabled, 0) > 0.5) counts.addrBaseRotation = 1;
  // 基础槽的片数(`_BaseMapSliceCount` / `_BaseMapProgress(Coord)`)在律里**只在
  // 数组档那一支出现**,二维档的 GLSL 里一次都不引用它。片索引由发射器一侧按层
  // 逐帧算(见 emoticon.js 的 `_arrayLayerIndex`),不在这条片元链上,所以这里不记。

  // 自发光槽。整条发光链是另一个 pass 的输出,本文件不做(`counts.emission`),
  // 于是这一槽的寻址**一项都没读**:两个逐粒子偏移、片数、进度。单独记一笔,
  // 否则「基础槽的寻址做对了」会被读成「寻址都做了」。
  if (acts('_EmissionMapOffsetXCoord') || acts('_EmissionMapOffsetYCoord')
      || acts('_EmissionMapProgressCoord') || nz('_EmissionMapProgress')
      || keywords.has('_EMISSION_MAP_MODE_2D_ARRAY') || textures._EmissionMap2DArray) {
    counts.addrEmissionUnread = 1;
  }
  // 顶点形变槽同理:改的是顶点位置,本文件不做,它那两个逐粒子偏移也就没读。
  if (keywords.has('_VERTEX_DEFORMATION_ENABLED')
      && (acts('_VertexDeformationMapOffsetXCoord') || acts('_VertexDeformationMapOffsetYCoord'))) {
    counts.addrVertexDeformUnread = 1;
  }
  // 下面三项是**原版就没有实现**的寻址,不是我们的缺口:过渡图的数组/三维取样与
  // 它的片数进度、染色图的片数与三维进度、整套视差。三份报告在两个平台的全部
  // 已发货变体里数过,这些名字出现 0 次。
  if (acts('_AlphaTransitionMapProgressCoord') || nz('_AlphaTransitionMapProgress')
      || textures._AlphaTransitionMap2DArray || textures._AlphaTransitionMap3D) {
    counts.addrUnshippedTransitionSlice = 1;
  }
  if (acts('_TintMap3DProgressCoord') || nz('_TintMap3DProgress') || textures._TintMap3D) {
    counts.addrUnshippedTintSlice = 1;
  }
  if (acts('_ParallaxMapOffsetXCoord') || acts('_ParallaxMapOffsetYCoord')
      || acts('_ParallaxMapProgressCoord') || nz('_ParallaxStrength')
      || textures._ParallaxMap || textures._ParallaxMap2DArray || textures._ParallaxMap3D) {
    counts.addrUnshippedParallax = 1;
  }

  // —— 染色档 ——
  // 区域模式为 0 的那一支**根本不做染色**,乘数恒为 1;贴图与材质色是互斥的替代关系,
  // 不是叠加。贴图那一路的乘数逐像素在 [0,1] 内,不会溢出,所以高动态那道闸只管材质色。
  let tintMode = 0;
  const useMap = keywords.has('_TINT_MAP_ENABLED');
  if (keywords.has('_TINT_AREA_ALL')) tintMode = 1;
  else if (keywords.has('_TINT_AREA_RIM')) tintMode = 2;
  let tintColor = colors._TintColor;
  if (tintMode && !useMap && !Array.isArray(tintColor)) { tintMode = 0; counts.tintRefused = 1; }
  if (tintMode && useMap && !textures._TintMap) { tintMode = 0; counts.tintRefused = 1; }
  if (tintMode && !useMap) {
    // 这个材质能达到的最大乘数:全域档混合率是常量,边缘档的权重上限是 1。
    const rate = Math.min(1, Math.max(0, n(floats._TintBlendRate, 0)));
    const top = tintMode === 2
      ? Math.min(1, Math.max(0, n(floats._TintBlendRate, 0) * n(floats._TintBlendRate, 0)))
      : rate;
    const mul = [0, 1, 2].map((i) => 1 + top * (n(tintColor[i], 1) - 1));
    if (mul.some((v) => v > 1.0001)) { tintMode = 0; counts.tintHdrUnrepresented = 1; }
  }
  if (tintMode) counts.tintApplied = 1;
  // 诊断读数,**不参与绘制**:这条链上只有「全域 + 材质色」那一档的乘数是整片同一个
  // 常量(边缘档按边缘权重逐像素给,贴图档逐像素取图)。有常量的时候把它算出来放这儿,
  // 探针要看「这个材质到底染了多少」时读它;绘制一律走着色器。
  const constantTint = (tintMode === 1 && !useMap && Array.isArray(tintColor))
    ? [0, 1, 2].map((i) => 1 + Math.min(1, Math.max(0, n(floats._TintBlendRate, 0)))
                               * (n(tintColor[i], 1) - 1))
    : null;

  // —— 过渡档 ——
  // 两个关键字互斥。过渡图在这一版里**只有二维取样**被编译过,所以只查二维那一槽;
  // 没绑图就没有可比的量,整段让开并计数(这不是「关代码路径」,是这个材质没给图)。
  let transMode = 0;
  if (keywords.has('_DISSOLVE_TRANSITION_ENABLED')) transMode = 1;
  else if (keywords.has('_FADE_TRANSITION_ENABLED')) transMode = 2;
  const transFile = textures._AlphaTransitionMap || null;
  if (transMode && !transFile) { counts.transitionNoMap = 1; transMode = 0; }
  if (transMode === 1) counts.transitionDissolve = 1;
  if (transMode === 2) counts.transitionFade = 1;

  const flowFile = textures._FlowMap || null;
  const flowToBase = keywords.has('_FLOW_MAP_TARGET_BASE') && flowFile ? 1 : 0;
  const flowToTrans = keywords.has('_FLOW_MAP_TARGET_ALPHA_TRANSITION') && flowFile && transMode
    ? 1 : 0;
  counts.flowToBase = flowToBase;
  counts.flowToTransition = flowToTrans;
  // 这两槽的逐粒子偏移**做到了**,但只有那一槽真的被采时才算数:没有过渡图就没有
  // uvTrans 的消费点,没有 flow 图就没有 uvFlow 的消费点。
  if (transMode && (acts('_AlphaTransitionMapOffsetXCoord')
                    || acts('_AlphaTransitionMapOffsetYCoord'))) {
    counts.addrTransitionOffset = 1;
  }
  if ((flowToBase || flowToTrans)
      && (acts('_FlowMapOffsetXCoord') || acts('_FlowMapOffsetYCoord'))) {
    counts.addrFlowOffset = 1;
  }

  const rimOn = keywords.has('_TRANSPARENCY_BY_RIM') ? 1 : 0;
  counts.rimTransparency = rimOn;
  // 亮度透明度不是关键字,是一个运行期分支;开关字段就是下面这一个。
  const lumOn = n(floats._TranceparencyByLuminanceEnabled, 0) > 0.5 ? 1 : 0;
  counts.luminance = lumOn;
  const fakeOn = n(floats._FakeLightEnabled, 0) > 0.5 ? 1 : 0;
  counts.fakeLight = fakeOn;

  const vertexAlphaProgress = keywords.has('_VERTEX_ALPHA_AS_TRANSITION_PROGRESS') && transMode
    ? 1 : 0;

  const rot = colors._BaseMapRotationOffsets;
  const fakeDir = colors._FakeLightDirection;
  const fakeCol = colors._FakeLightColor;
  const baseST = ctx.baseST || [1, 1, 0, 0];

  const uniforms = {
    uColor: { value: new THREE.Color(1, 1, 1) },
    uAlpha: { value: 1 },
    uCustom1: { value: new THREE.Vector4(0, 0, 0, 0) },
    uCustom2: { value: new THREE.Vector4(0, 0, 0, 0) },
    uSheet: { value: new THREE.Vector4(1, 1, 0, 0) },
    uRotation: { value: 0 },

    uBaseMap: { value: white },
    uBaseST: { value: new THREE.Vector4(baseST[0], baseST[1], baseST[2], baseST[3]) },
    uBaseRotEnabled: { value: n(floats._BaseMapRotationEnabled, 0) > 0.5 ? 1 : 0 },
    uBaseRotation: { value: n(floats._BaseMapRotation, 0) },
    uBaseRotCoord: { value: n(floats._BaseMapRotationCoord, 0) },
    uBaseRotPivot: { value: new THREE.Vector2(n(rot && rot[0], 0), n(rot && rot[1], 0)) },
    uBaseOffsetCoord: { value: new THREE.Vector2(n(floats._BaseMapOffsetXCoord, 0),
                                                 n(floats._BaseMapOffsetYCoord, 0)) },

    uTintMode: { value: tintMode },
    uTintUseMap: { value: tintMode && useMap ? 1 : 0 },
    uTintMap: { value: white },
    uTintMapST: { value: new THREE.Vector4(...st4(record, '_TintMap')) },
    uTintColor: { value: new THREE.Vector4(n(tintColor && tintColor[0], 1),
                                           n(tintColor && tintColor[1], 1),
                                           n(tintColor && tintColor[2], 1),
                                           n(tintColor && tintColor[3], 1)) },
    uTintBlendRate: { value: n(floats._TintBlendRate, 0) },
    uTintBlendRateCoord: { value: n(floats._TintBlendRateCoord, 0) },
    uTintRimProgress: { value: n(floats._TintRimProgress, 0) },
    uTintRimProgressCoord: { value: n(floats._TintRimProgressCoord, 0) },
    uTintRimSharpness: { value: n(floats._TintRimSharpness, 0) },
    uTintRimSharpnessCoord: { value: n(floats._TintRimSharpnessCoord, 0) },
    uInverseTintRim: { value: n(floats._InverseTintRim, 0) },

    uFakeLightEnabled: { value: fakeOn },
    uFakeLightDirection: { value: new THREE.Vector3(n(fakeDir && fakeDir[0], 0),
                                                    n(fakeDir && fakeDir[1], 0),
                                                    n(fakeDir && fakeDir[2], 0)) },
    uFakeLightColor: { value: new THREE.Vector4(n(fakeCol && fakeCol[0], 1),
                                                n(fakeCol && fakeCol[1], 1),
                                                n(fakeCol && fakeCol[2], 1),
                                                n(fakeCol && fakeCol[3], 1)) },

    uTransMode: { value: transMode },
    uTransMap: { value: white },
    uTransST: { value: new THREE.Vector4(...st4(record, '_AlphaTransitionMap')) },
    uTransOffsetCoord: { value: new THREE.Vector2(n(floats._AlphaTransitionMapOffsetXCoord, 0),
                                                  n(floats._AlphaTransitionMapOffsetYCoord, 0)) },
    uTransChannel: { value: n(floats._AlphaTransitionMapChannelsX, 0) },
    uTransProgress: { value: n(floats._AlphaTransitionProgress, 0) },
    uTransProgressCoord: { value: n(floats._AlphaTransitionProgressCoord, 0) },
    uDissolveSharpness: { value: n(floats._DissolveSharpness, 0) },
    uVertexAlphaAsProgress: { value: vertexAlphaProgress },

    uFlowToBase: { value: flowToBase },
    uFlowToTrans: { value: flowToTrans },
    uFlowMap: { value: white },
    uFlowST: { value: new THREE.Vector4(...st4(record, '_FlowMap')) },
    uFlowOffsetCoord: { value: new THREE.Vector2(n(floats._FlowMapOffsetXCoord, 0),
                                                 n(floats._FlowMapOffsetYCoord, 0)) },
    uFlowIntensity: { value: n(floats._FlowIntensity, 0) },
    uFlowIntensityCoord: { value: n(floats._FlowIntensityCoord, 0) },
    uFlowChannelX: { value: n(floats._FlowMapChannelsX, 0) },
    uFlowChannelY: { value: n(floats._FlowMapChannelsY, 1) },

    uRimTransparency: { value: rimOn },
    uRimProgress: { value: n(floats._RimTransparencyProgress, 0) },
    uRimProgressCoord: { value: n(floats._RimTransparencyProgressCoord, 0) },
    uRimSharpness: { value: n(floats._RimTransparencySharpness, 0) },
    uRimSharpnessCoord: { value: n(floats._RimTransparencySharpnessCoord, 0) },
    uInverseRimTransparency: { value: n(floats._InverseRimTransparency, 0) },

    uLuminance: { value: lumOn },
    uLumProgress: { value: n(floats._LuminanceTransparencyProgress, 0) },
    uLumProgressCoord: { value: n(floats._LuminanceTransparencyProgressCoord, 0) },
    uLumSharpness: { value: n(floats._LuminanceTransparencySharpness, 0) },
    uLumSharpnessCoord: { value: n(floats._LuminanceTransparencySharpnessCoord, 0) },
    uInverseLuminance: { value: n(floats._InverseLuminanceTransparency, 0) },
  };

  const billboard = (ctx.mode || 'Billboard') === 'Billboard';
  const build = (vertexShader) => {
    const m = new THREE.ShaderMaterial({
      uniforms,
      vertexShader,
      fragmentShader: FRAGMENT,
      transparent: true,
      blending: st.blending,
      blendSrc: st.blendSrc,
      blendDst: st.blendDst,
      blendEquation: st.blendEquation,
      premultipliedAlpha: st.premultipliedAlpha,
      depthWrite: st.depthWrite,
      depthTest: st.depthTest,
      // 贴地的片从上下两侧都看得见,网格件同理;朝相机的四边形与这一项无关。
      side: THREE.DoubleSide,
    });
    return ctx.applyZOffset ? ctx.applyZOffset(m, st.zOffset) : m;
  };
  const material = build(billboard ? VERTEX_BILLBOARD : VERTEX_SHAPED);

  /** 这个发射器要不要逐帧算逐粒子向量(材质里有没有指向它们的选择器)。 */
  const coordFields = [
    '_BaseMapRotationCoord', '_BaseMapOffsetXCoord', '_BaseMapOffsetYCoord',
    '_TintBlendRateCoord', '_TintRimProgressCoord', '_TintRimSharpnessCoord',
    '_AlphaTransitionProgressCoord', '_AlphaTransitionMapOffsetXCoord',
    '_AlphaTransitionMapOffsetYCoord', '_FlowIntensityCoord',
    '_FlowMapOffsetXCoord', '_FlowMapOffsetYCoord',
    '_RimTransparencyProgressCoord', '_RimTransparencySharpnessCoord',
    '_LuminanceTransparencyProgressCoord', '_LuminanceTransparencySharpnessCoord',
  ];
  const coords = [];
  for (const key of coordFields) {
    const source = coordSource(floats[key]);
    if (source) coords.push({ key, ...source });
  }

  const handle = {
    material,
    uniforms,
    counts,
    coords,
    needsCustom: coords.length > 0,
    tintMode,
    transMode,
    constantTint,

    /** 一颗粒子的可变量。材质是共用的,这些每次绘制前才写进 uniform。 */
    makeState() {
      return { color: new THREE.Color(1, 1, 1), opacity: 1, rotation: 0, map: null,
               sheet: [1, 1, 0, 0], custom1: [0, 0, 0, 0], custom2: [0, 0, 0, 0] };
    },

    /** 把一颗粒子的可变量写进共用材质。渲染前一刻调用。 */
    push(s) {
      uniforms.uColor.value.copy(s.color);
      uniforms.uAlpha.value = s.opacity;
      uniforms.uRotation.value = s.rotation;
      uniforms.uSheet.value.set(s.sheet[0], s.sheet[1], s.sheet[2], s.sheet[3]);
      uniforms.uCustom1.value.set(s.custom1[0], s.custom1[1], s.custom1[2], s.custom1[3]);
      uniforms.uCustom2.value.set(s.custom2[0], s.custom2[1], s.custom2[2], s.custom2[3]);
      if (s.map) uniforms.uBaseMap.value = s.map;
      material.uniformsNeedUpdate = true;
    },

    /** 把一个可绘制对象与一颗粒子的可变量绑起来。 */
    bind(object, state) {
      object.onBeforeRender = () => handle.push(state);
    },

    /** 固定槽位的贴图(整条发射器共用)。基础图是逐粒子的,走 `push`。 */
    setSlotTextures(textureFor) {
      if (transFile) uniforms.uTransMap.value = textureFor(transFile) || white;
      if (useMap && textures._TintMap) uniforms.uTintMap.value = textureFor(textures._TintMap) || white;
      if (flowFile) uniforms.uFlowMap.value = textureFor(flowFile) || white;
    },

    /**
     * 诊断用:把这条链画进一张离屏图,读回每个像素的 alpha。
     *
     * 画的是一块正对镜头的方片、uv 铺满 0..1,与一颗粒子的四边形同构;混合被关掉,
     * 所以读回来的就是片元链算出来的 alpha 本身,不掺背景。
     *
     * **注意**:方片没有网格件的真实法线,所以对网格绘制模式的发射器,这里的边缘量
     * 是方片自己的,不是原件的。uv 驱动的那几段(基础图、过渡图、染色图)是准的。
     */
    probe(renderer, options) {
      const opt = options || {};
      const size = Math.max(3, Math.round(opt.size || 33));
      const probeMat = build(VERTEX_SHAPED);
      probeMat.blending = THREE.NoBlending;
      probeMat.transparent = false;
      probeMat.depthTest = false;
      probeMat.depthWrite = false;
      const scene = new THREE.Scene();
      const geo = new THREE.PlaneGeometry(1, 1);
      scene.add(new THREE.Mesh(geo, probeMat));
      const cam = new THREE.OrthographicCamera(-0.5, 0.5, 0.5, -0.5, 1, 4000);
      cam.position.set(0, 0, 2000);          // 远处正对:视线方向在整片上几乎不变
      cam.lookAt(0, 0, 0);
      cam.updateMatrixWorld(true);
      // 浮点靶:八位靶把每个读数量化到 1/255,而这条链末尾常有很小的因子(边缘、
      // 亮度),量化后几个真实取值会挤成同一个数,判据就读不出差别。拿不到浮点靶时
      // 退回八位并在结果里标出来,不假装精度。
      const float = !!(renderer.capabilities && renderer.capabilities.isWebGL2
        && renderer.extensions && renderer.extensions.has('EXT_color_buffer_float'));
      const rt = new THREE.WebGLRenderTarget(size, size,
        float ? { type: THREE.FloatType } : undefined);
      handle.push({
        color: new THREE.Color(...(opt.color || [1, 1, 1])),
        opacity: opt.alpha === undefined ? 1 : opt.alpha,
        rotation: opt.rotation || 0,
        map: opt.map || uniforms.uBaseMap.value,
        sheet: opt.sheet || [1, 1, 0, 0],
        custom1: opt.custom1 || [0, 0, 0, 0],
        custom2: opt.custom2 || [0, 0, 0, 0],
      });
      probeMat.uniformsNeedUpdate = true;
      const prevTarget = renderer.getRenderTarget();
      const prevColor = renderer.getClearColor(new THREE.Color());
      const prevAlpha = renderer.getClearAlpha();
      renderer.setRenderTarget(rt);
      renderer.setClearColor(0x000000, 0);
      renderer.clear(true, true, false);
      renderer.render(scene, cam);
      const buf = float ? new Float32Array(size * size * 4) : new Uint8Array(size * size * 4);
      renderer.readRenderTargetPixels(rt, 0, 0, size, size, buf);
      renderer.setRenderTarget(prevTarget);
      renderer.setClearColor(prevColor, prevAlpha);
      rt.dispose();
      geo.dispose();
      probeMat.dispose();
      const alpha = new Array(size * size);
      // 颜色也读回来。alpha 那一路在有些材质上是平的(基础图的取样窗整块都是纯白
      // 实心,那正是这个族的常见写法),这时候寻址对不对在 alpha 上一个字也看不出来,
      // 只在 RGB 上看得出来 —— 读回来才判得动。
      const rgb = new Array(size * size * 3);
      for (let i = 0; i < size * size; i++) {
        alpha[i] = float ? buf[i * 4 + 3] : buf[i * 4 + 3] / 255;
        for (let c = 0; c < 3; c++) {
          rgb[i * 3 + c] = float ? buf[i * 4 + c] : buf[i * 4 + c] / 255;
        }
      }
      const at = (x, y) => alpha[y * size + x];
      const e = 0;
      return {
        size,
        float,
        alpha,
        rgb,
        center: at((size - 1) >> 1, (size - 1) >> 1),
        corners: [at(e, e), at(size - 1 - e, e), at(e, size - 1 - e), at(size - 1 - e, size - 1 - e)],
        min: Math.min(...alpha),
        max: Math.max(...alpha),
        rgbMin: Math.min(...rgb),
        rgbMax: Math.max(...rgb),
      };
    },

    dispose() { material.dispose(); },
  };
  return handle;
}
