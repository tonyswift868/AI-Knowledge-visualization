import{An as e,At as t,Cn as n,Ct as r,Dn as i,Dt as a,En as o,G as s,I as c,In as l,J as u,L as d,Ln as f,Pn as ee,Q as te,Qt as ne,Rt as re,Sn as ie,St as p,Tt as ae,Ut as oe,Vt as se,X as m,Y as ce,Z as le,fn as h,gn as ue,hn as g,kn as _,kt as de,ln as v,lt as fe,nt as pe,on as me,pn as he,r as ge,rn as y,s as _e,sn as b,t as ve,un as x,ut as ye,yt as be}from"./colorToUniform-CIzLOklp.js";var xe=class e extends ce{constructor(t){t={...e.defaultOptions,...t},super(t),this.enabled=!0,this._state=u.for2d(),this.blendMode=t.blendMode,this.padding=t.padding,typeof t.antialias==`boolean`?this.antialias=t.antialias?`on`:`off`:this.antialias=t.antialias,this.resolution=t.resolution,this.blendRequired=t.blendRequired,this.clipToViewport=t.clipToViewport,this.addResource(`uTexture`,0,1)}apply(e,t,n,r){e.applyFilter(this,t,n,r)}get blendMode(){return this._state.blendMode}set blendMode(e){this._state.blendMode=e}static from(t){let{gpu:n,gl:r,...i}=t,a,o;return n&&(a=pe.from(n)),r&&(o=ye.from(r)),new e({gpuProgram:a,glProgram:o,...i})}};xe.defaultOptions={blendMode:`normal`,resolution:1,padding:0,antialias:`off`,blendRequired:!1,clipToViewport:!0};var Se=xe,S=[];f.handleByNamedList(l.Environment,S);async function C(e){if(!e)for(let e=0;e<S.length;e++){let t=S[e];if(t.value.test()){await t.value.load();return}}}async function Ce(e){return C(!e)}var w;function T(){if(typeof w==`boolean`)return w;try{w=Function(`param1`,`param2`,`param3`,`return param1[param2] === param3;`)({a:`b`},`a`,`b`)===!0}catch{w=!1}return w}var E=(e=>(e[e.NONE=0]=`NONE`,e[e.COLOR=16384]=`COLOR`,e[e.STENCIL=1024]=`STENCIL`,e[e.DEPTH=256]=`DEPTH`,e[e.COLOR_DEPTH=16640]=`COLOR_DEPTH`,e[e.COLOR_STENCIL=17408]=`COLOR_STENCIL`,e[e.DEPTH_STENCIL=1280]=`DEPTH_STENCIL`,e[e.ALL=17664]=`ALL`,e))(E||{}),D=class{constructor(e){this.items=[],this._name=e}emit(e,t,n,r,i,a,o,s){let{name:c,items:l}=this;for(let u=0,d=l.length;u<d;u++)l[u][c](e,t,n,r,i,a,o,s);return this}add(e){return e[this._name]&&(this.remove(e),this.items.push(e)),this}remove(e){let t=this.items.indexOf(e);return t!==-1&&this.items.splice(t,1),this}contains(e){return this.items.indexOf(e)!==-1}removeAll(){return this.items.length=0,this}destroy(){this.removeAll(),this.items=null,this._name=null}get empty(){return this.items.length===0}get name(){return this._name}},we=[`init`,`destroy`,`contextChange`,`resolutionChange`,`resetState`,`renderEnd`,`renderStart`,`render`,`update`,`postrender`,`prerender`],Te=class e extends ee{constructor(e){super(),this.uid=o(`renderer`),this.runners=Object.create(null),this.renderPipes=Object.create(null),this._initOptions={},this._systemsHash=Object.create(null),this.type=e.type,this.name=e.name,this.config=e;let t=[...we,...this.config.runners??[]];this._addRunners(...t),this._unsafeEvalCheck()}async init(t={}){await C(t.skipExtensionImports===!0||t.manageImports===!1),this._addSystems(this.config.systems),this._addPipes(this.config.renderPipes,this.config.renderPipeAdaptors);for(let e in this._systemsHash)t={...this._systemsHash[e].constructor.defaultOptions,...t};t={...e.defaultOptions,...t},this._roundPixels=+!!t.roundPixels;for(let e=0;e<this.runners.init.items.length;e++)await this.runners.init.items[e].init(t);this._initOptions=t}render(e,t){let r=e;if(r instanceof a&&(r={container:r},t&&(ie(n,`passing a second argument is deprecated, please use render options instead`),r.target=t.renderTexture)),r.target||=this.view.renderTarget,r.target===this.view.renderTarget&&(this._lastObjectRendered=r.container,r.clearColor??=this.background.colorRgba,r.clear??=this.background.clearBeforeRender),r.clearColor){let e=Array.isArray(r.clearColor)&&r.clearColor.length===4;r.clearColor=e?r.clearColor:v.shared.setValue(r.clearColor).toArray()}r.transform||(r.container.updateLocalTransform(),r.transform=r.container.localTransform),r.container.visible&&(r.container.enableRenderGroup(),this.runners.prerender.emit(r),this.runners.renderStart.emit(r),this.runners.render.emit(r),this.runners.renderEnd.emit(r),this.runners.postrender.emit(r))}resize(e,t,n){let r=this.view.resolution;this.view.resize(e,t,n),this.emit(`resize`,this.view.screen.width,this.view.screen.height,this.view.resolution),n!==void 0&&n!==r&&this.runners.resolutionChange.emit(n)}clear(e={}){let t=this;e.target||=t.renderTarget.renderTarget,e.clearColor||=this.background.colorRgba,e.clear??=E.ALL;let{clear:n,clearColor:r,target:i}=e;v.shared.setValue(r??this.background.colorRgba),t.renderTarget.clear(i,n,v.shared.toArray())}get resolution(){return this.view.resolution}set resolution(e){this.view.resolution=e,this.runners.resolutionChange.emit(e)}get width(){return this.view.texture.frame.width}get height(){return this.view.texture.frame.height}get canvas(){return this.view.canvas}get lastObjectRendered(){return this._lastObjectRendered}get renderingToScreen(){return this.renderTarget.renderingToScreen}get screen(){return this.view.screen}_addRunners(...e){e.forEach(e=>{this.runners[e]=new D(e)})}_addSystems(e){let t;for(t in e){let n=e[t];this._addSystem(n.value,n.name)}}_addSystem(e,t){let n=new e(this);if(this[t])throw Error(`Whoops! The name "${t}" is already in use`);this[t]=n,this._systemsHash[t]=n;for(let e in this.runners)this.runners[e].add(n);return this}_addPipes(e,t){let n=t.reduce((e,t)=>(e[t.name]=t.value,e),{});e.forEach(e=>{let t=e.value,r=e.name,i=n[r];this.renderPipes[r]=new t(this,i?new i:null),this.runners.destroy.add(this.renderPipes[r])})}destroy(e=!1){this.runners.destroy.items.reverse(),this.runners.destroy.emit(e),Object.values(this.runners).forEach(e=>{e.destroy()}),(e===!0||typeof e==`object`&&e.releaseGlobalResources)&&me.release(),this._systemsHash=null,this.renderPipes=null}generateTexture(e){return this.textureGenerator.generateTexture(e)}get roundPixels(){return!!this._roundPixels}_unsafeEvalCheck(){if(!T())throw Error(`Current environment does not allow unsafe-eval, please use pixi.js/unsafe-eval module to enable support.`)}resetState(){this.runners.resetState.emit()}};Te.defaultOptions={resolution:1,failIfMajorPerformanceCaveat:!1,roundPixels:!1};var Ee=Te,De=/^\s*data:(?:([\w-]+)\/([\w+.-]+))?(?:;charset=([\w-]+))?(?:;(base64))?,(.*)/i,O=`8.13.1`,Oe=class{static init(){globalThis.__PIXI_APP_INIT__?.(this,O)}static destroy(){}};Oe.extension=l.Application;var k=class{constructor(e){this._renderer=e}init(){globalThis.__PIXI_RENDERER_INIT__?.(this._renderer,O)}destroy(){this._renderer=null}};k.extension={type:[l.WebGLSystem,l.WebGPUSystem],name:`initHook`,priority:-10};var ke=`in vec2 vMaskCoord;
in vec2 vTextureCoord;

uniform sampler2D uTexture;
uniform sampler2D uMaskTexture;

uniform float uAlpha;
uniform vec4 uMaskClamp;
uniform float uInverse;

out vec4 finalColor;

void main(void)
{
    float clip = step(3.5,
        step(uMaskClamp.x, vMaskCoord.x) +
        step(uMaskClamp.y, vMaskCoord.y) +
        step(vMaskCoord.x, uMaskClamp.z) +
        step(vMaskCoord.y, uMaskClamp.w));

    // TODO look into why this is needed
    float npmAlpha = uAlpha;
    vec4 original = texture(uTexture, vTextureCoord);
    vec4 masky = texture(uMaskTexture, vMaskCoord);
    float alphaMul = 1.0 - npmAlpha * (1.0 - masky.a);

    float a = alphaMul * masky.r * npmAlpha * clip;

    if (uInverse == 1.0) {
        a = 1.0 - a;
    }

    finalColor = original * a;
}
`,Ae=`in vec2 aPosition;

out vec2 vTextureCoord;
out vec2 vMaskCoord;


uniform vec4 uInputSize;
uniform vec4 uOutputFrame;
uniform vec4 uOutputTexture;
uniform mat3 uFilterMatrix;

vec4 filterVertexPosition(  vec2 aPosition )
{
    vec2 position = aPosition * uOutputFrame.zw + uOutputFrame.xy;
       
    position.x = position.x * (2.0 / uOutputTexture.x) - 1.0;
    position.y = position.y * (2.0*uOutputTexture.z / uOutputTexture.y) - uOutputTexture.z;

    return vec4(position, 0.0, 1.0);
}

vec2 filterTextureCoord(  vec2 aPosition )
{
    return aPosition * (uOutputFrame.zw * uInputSize.zw);
}

vec2 getFilterCoord( vec2 aPosition )
{
    return  ( uFilterMatrix * vec3( filterTextureCoord(aPosition), 1.0)  ).xy;
}   

void main(void)
{
    gl_Position = filterVertexPosition(aPosition);
    vTextureCoord = filterTextureCoord(aPosition);
    vMaskCoord = getFilterCoord(aPosition);
}
`,A=`struct GlobalFilterUniforms {
  uInputSize:vec4<f32>,
  uInputPixel:vec4<f32>,
  uInputClamp:vec4<f32>,
  uOutputFrame:vec4<f32>,
  uGlobalFrame:vec4<f32>,
  uOutputTexture:vec4<f32>,
};

struct MaskUniforms {
  uFilterMatrix:mat3x3<f32>,
  uMaskClamp:vec4<f32>,
  uAlpha:f32,
  uInverse:f32,
};

@group(0) @binding(0) var<uniform> gfu: GlobalFilterUniforms;
@group(0) @binding(1) var uTexture: texture_2d<f32>;
@group(0) @binding(2) var uSampler : sampler;

@group(1) @binding(0) var<uniform> filterUniforms : MaskUniforms;
@group(1) @binding(1) var uMaskTexture: texture_2d<f32>;

struct VSOutput {
    @builtin(position) position: vec4<f32>,
    @location(0) uv : vec2<f32>,
    @location(1) filterUv : vec2<f32>,
};

fn filterVertexPosition(aPosition:vec2<f32>) -> vec4<f32>
{
    var position = aPosition * gfu.uOutputFrame.zw + gfu.uOutputFrame.xy;

    position.x = position.x * (2.0 / gfu.uOutputTexture.x) - 1.0;
    position.y = position.y * (2.0*gfu.uOutputTexture.z / gfu.uOutputTexture.y) - gfu.uOutputTexture.z;

    return vec4(position, 0.0, 1.0);
}

fn filterTextureCoord( aPosition:vec2<f32> ) -> vec2<f32>
{
    return aPosition * (gfu.uOutputFrame.zw * gfu.uInputSize.zw);
}

fn globalTextureCoord( aPosition:vec2<f32> ) -> vec2<f32>
{
  return  (aPosition.xy / gfu.uGlobalFrame.zw) + (gfu.uGlobalFrame.xy / gfu.uGlobalFrame.zw);
}

fn getFilterCoord(aPosition:vec2<f32> ) -> vec2<f32>
{
  return ( filterUniforms.uFilterMatrix * vec3( filterTextureCoord(aPosition), 1.0)  ).xy;
}

fn getSize() -> vec2<f32>
{
  return gfu.uGlobalFrame.zw;
}

@vertex
fn mainVertex(
  @location(0) aPosition : vec2<f32>,
) -> VSOutput {
  return VSOutput(
   filterVertexPosition(aPosition),
   filterTextureCoord(aPosition),
   getFilterCoord(aPosition)
  );
}

@fragment
fn mainFragment(
  @location(0) uv: vec2<f32>,
  @location(1) filterUv: vec2<f32>,
  @builtin(position) position: vec4<f32>
) -> @location(0) vec4<f32> {

    var maskClamp = filterUniforms.uMaskClamp;
    var uAlpha = filterUniforms.uAlpha;

    var clip = step(3.5,
      step(maskClamp.x, filterUv.x) +
      step(maskClamp.y, filterUv.y) +
      step(filterUv.x, maskClamp.z) +
      step(filterUv.y, maskClamp.w));

    var mask = textureSample(uMaskTexture, uSampler, filterUv);
    var source = textureSample(uTexture, uSampler, uv);
    var alphaMul = 1.0 - uAlpha * (1.0 - mask.a);

    var a: f32 = alphaMul * mask.r * uAlpha * clip;

    if (filterUniforms.uInverse == 1.0) {
        a = 1.0 - a;
    }

    return source * a;
}
`,je=class extends Se{constructor(e){let{sprite:t,...n}=e,r=new he(t.texture),i=new te({uFilterMatrix:{value:new _,type:`mat3x3<f32>`},uMaskClamp:{value:r.uClampFrame,type:`vec4<f32>`},uAlpha:{value:1,type:`f32`},uInverse:{value:+!!e.inverse,type:`f32`}}),a=pe.from({vertex:{source:A,entryPoint:`mainVertex`},fragment:{source:A,entryPoint:`mainFragment`}}),o=ye.from({vertex:Ae,fragment:ke,name:`mask-filter`});super({...n,gpuProgram:a,glProgram:o,clipToViewport:!1,resources:{filterUniforms:i,uMaskTexture:t.texture.source}}),this.sprite=t,this._textureMatrix=r}set inverse(e){this.resources.filterUniforms.uniforms.uInverse=+!!e}get inverse(){return this.resources.filterUniforms.uniforms.uInverse===1}apply(e,t,n,r){this._textureMatrix.texture=this.sprite.texture,e.calculateSpriteMatrix(this.resources.filterUniforms.uniforms.uFilterMatrix,this.sprite).prepend(this._textureMatrix.mapCoord),this.resources.uMaskTexture=this.sprite.texture.source,e.applyFilter(this,t,n,r)}},j=class e{constructor(e,t){this.state=u.for2d(),this._batchersByInstructionSet=Object.create(null),this._activeBatches=Object.create(null),this.renderer=e,this._adaptor=t,this._adaptor.init?.(this)}static getBatcher(e){return new this._availableBatchers[e]}buildStart(e){let t=this._batchersByInstructionSet[e.uid];t||(t=this._batchersByInstructionSet[e.uid]=Object.create(null),t.default||=new _e({maxTextures:this.renderer.limits.maxBatchableTextures})),this._activeBatches=t,this._activeBatch=this._activeBatches.default;for(let e in this._activeBatches)this._activeBatches[e].begin()}addToBatch(t,n){if(this._activeBatch.name!==t.batcherName){this._activeBatch.break(n);let r=this._activeBatches[t.batcherName];r||(r=this._activeBatches[t.batcherName]=e.getBatcher(t.batcherName),r.begin()),this._activeBatch=r}this._activeBatch.add(t)}break(e){this._activeBatch.break(e)}buildEnd(e){this._activeBatch.break(e);let t=this._activeBatches;for(let e in t){let n=t[e],r=n.geometry;r.indexBuffer.setDataWithSize(n.indexBuffer,n.indexSize,!0),r.buffers[0].setDataWithSize(n.attributeBuffer.float32View,n.attributeSize,!1)}}upload(e){let t=this._batchersByInstructionSet[e.uid];for(let e in t){let n=t[e],r=n.geometry;n.dirty&&(n.dirty=!1,r.buffers[0].update(n.attributeSize*4))}}execute(e){if(e.action===`startBatch`){let t=e.batcher,n=t.geometry,r=t.shader;this._adaptor.start(this,n,r)}this._adaptor.execute(this,e)}destroy(){this.state=null,this.renderer=null,this._adaptor=null;for(let e in this._activeBatches)this._activeBatches[e].destroy();this._activeBatches=null}};j.extension={type:[l.WebGLPipes,l.WebGPUPipes,l.CanvasPipes],name:`batch`},j._availableBatchers=Object.create(null);var M=j;f.handleByMap(l.Batcher,M._availableBatchers),f.add(_e);var Me={name:`texture-bit`,vertex:{header:`

        struct TextureUniforms {
            uTextureMatrix:mat3x3<f32>,
        }

        @group(2) @binding(2) var<uniform> textureUniforms : TextureUniforms;
        `,main:`
            uv = (textureUniforms.uTextureMatrix * vec3(uv, 1.0)).xy;
        `},fragment:{header:`
            @group(2) @binding(0) var uTexture: texture_2d<f32>;
            @group(2) @binding(1) var uSampler: sampler;


        `,main:`
            outColor = textureSample(uTexture, uSampler, vUV);
        `}},Ne={name:`texture-bit`,vertex:{header:`
            uniform mat3 uTextureMatrix;
        `,main:`
            uv = (uTextureMatrix * vec3(uv, 1.0)).xy;
        `},fragment:{header:`
        uniform sampler2D uTexture;


        `,main:`
            outColor = texture(uTexture, vUV);
        `}},Pe=new x,Fe=class extends ne{constructor(){super(),this.filters=[new je({sprite:new ae(h.EMPTY),inverse:!1,resolution:`inherit`,antialias:`inherit`})]}get sprite(){return this.filters[0].sprite}set sprite(e){this.filters[0].sprite=e}get inverse(){return this.filters[0].inverse}set inverse(e){this.filters[0].inverse=e}},N=class{constructor(e){this._activeMaskStage=[],this._renderer=e}push(e,t,n){let r=this._renderer;if(r.renderPipes.batch.break(n),n.add({renderPipeId:`alphaMask`,action:`pushMaskBegin`,mask:e,inverse:t._maskOptions.inverse,canBundle:!1,maskedContainer:t}),e.inverse=t._maskOptions.inverse,e.renderMaskToTexture){let t=e.mask;t.includeInBuild=!0,t.collectRenderables(n,r,null),t.includeInBuild=!1}r.renderPipes.batch.break(n),n.add({renderPipeId:`alphaMask`,action:`pushMaskEnd`,mask:e,maskedContainer:t,inverse:t._maskOptions.inverse,canBundle:!1})}pop(e,t,n){this._renderer.renderPipes.batch.break(n),n.add({renderPipeId:`alphaMask`,action:`popMaskEnd`,mask:e,inverse:t._maskOptions.inverse,canBundle:!1})}execute(e){let n=this._renderer,r=e.mask.renderMaskToTexture;if(e.action===`pushMaskBegin`){let i=y.get(Fe);if(i.inverse=e.inverse,r){e.mask.mask.measurable=!0;let r=oe(e.mask.mask,!0,Pe);e.mask.mask.measurable=!1,r.ceil();let a=n.renderTarget.renderTarget.colorTexture.source,o=t.getOptimalTexture(r.width,r.height,a._resolution,a.antialias);n.renderTarget.push(o,!0),n.globalUniforms.push({offset:r,worldColor:4294967295});let s=i.sprite;s.texture=o,s.worldTransform.tx=r.minX,s.worldTransform.ty=r.minY,this._activeMaskStage.push({filterEffect:i,maskedContainer:e.maskedContainer,filterTexture:o})}else i.sprite=e.mask.mask,this._activeMaskStage.push({filterEffect:i,maskedContainer:e.maskedContainer})}else if(e.action===`pushMaskEnd`){let e=this._activeMaskStage[this._activeMaskStage.length-1];r&&(n.type===m.WEBGL&&n.renderTarget.finishRenderPass(),n.renderTarget.pop(),n.globalUniforms.pop()),n.filter.push({renderPipeId:`filter`,action:`pushFilter`,container:e.maskedContainer,filterEffect:e.filterEffect,canBundle:!1})}else if(e.action===`popMaskEnd`){n.filter.pop();let e=this._activeMaskStage.pop();r&&t.returnTexture(e.filterTexture),y.return(e.filterEffect)}}destroy(){this._renderer=null,this._activeMaskStage=null}};N.extension={type:[l.WebGLPipes,l.WebGPUPipes,l.CanvasPipes],name:`alphaMask`};var P=class{constructor(e){this._colorStack=[],this._colorStackIndex=0,this._currentColor=0,this._renderer=e}buildStart(){this._colorStack[0]=15,this._colorStackIndex=1,this._currentColor=15}push(e,t,n){this._renderer.renderPipes.batch.break(n);let r=this._colorStack;r[this._colorStackIndex]=r[this._colorStackIndex-1]&e.mask;let i=this._colorStack[this._colorStackIndex];i!==this._currentColor&&(this._currentColor=i,n.add({renderPipeId:`colorMask`,colorMask:i,canBundle:!1})),this._colorStackIndex++}pop(e,t,n){this._renderer.renderPipes.batch.break(n);let r=this._colorStack;this._colorStackIndex--;let i=r[this._colorStackIndex-1];i!==this._currentColor&&(this._currentColor=i,n.add({renderPipeId:`colorMask`,colorMask:i,canBundle:!1}))}execute(e){this._renderer.colorMask.setMask(e.colorMask)}destroy(){this._renderer=null,this._colorStack=null}};P.extension={type:[l.WebGLPipes,l.WebGPUPipes,l.CanvasPipes],name:`colorMask`};var F=class{constructor(e){this._maskStackHash={},this._maskHash=new WeakMap,this._renderer=e}push(e,t,n){var r;let i=e,a=this._renderer;a.renderPipes.batch.break(n),a.renderPipes.blendMode.setBlendMode(i.mask,`none`,n),n.add({renderPipeId:`stencilMask`,action:`pushMaskBegin`,mask:e,inverse:t._maskOptions.inverse,canBundle:!1});let o=i.mask;o.includeInBuild=!0,this._maskHash.has(i)||this._maskHash.set(i,{instructionsStart:0,instructionsLength:0});let s=this._maskHash.get(i);s.instructionsStart=n.instructionSize,o.collectRenderables(n,a,null),o.includeInBuild=!1,a.renderPipes.batch.break(n),n.add({renderPipeId:`stencilMask`,action:`pushMaskEnd`,mask:e,inverse:t._maskOptions.inverse,canBundle:!1}),s.instructionsLength=n.instructionSize-s.instructionsStart-1;let c=a.renderTarget.renderTarget.uid;(r=this._maskStackHash)[c]??(r[c]=0)}pop(e,t,n){let r=e,i=this._renderer;i.renderPipes.batch.break(n),i.renderPipes.blendMode.setBlendMode(r.mask,`none`,n),n.add({renderPipeId:`stencilMask`,action:`popMaskBegin`,inverse:t._maskOptions.inverse,canBundle:!1});let a=this._maskHash.get(e);for(let e=0;e<a.instructionsLength;e++)n.instructions[n.instructionSize++]=n.instructions[a.instructionsStart++];n.add({renderPipeId:`stencilMask`,action:`popMaskEnd`,canBundle:!1})}execute(e){var t;let n=this._renderer,r=n.renderTarget.renderTarget.uid,i=(t=this._maskStackHash)[r]??(t[r]=0);e.action===`pushMaskBegin`?(n.renderTarget.ensureDepthStencil(),n.stencil.setStencilMode(s.RENDERING_MASK_ADD,i),i++,n.colorMask.setMask(0)):e.action===`pushMaskEnd`?(e.inverse?n.stencil.setStencilMode(s.INVERSE_MASK_ACTIVE,i):n.stencil.setStencilMode(s.MASK_ACTIVE,i),n.colorMask.setMask(15)):e.action===`popMaskBegin`?(n.colorMask.setMask(0),i===0?(n.renderTarget.clear(null,E.STENCIL),n.stencil.setStencilMode(s.DISABLED,i)):n.stencil.setStencilMode(s.RENDERING_MASK_REMOVE,i),i--):e.action===`popMaskEnd`&&(e.inverse?n.stencil.setStencilMode(s.INVERSE_MASK_ACTIVE,i):n.stencil.setStencilMode(s.MASK_ACTIVE,i),n.colorMask.setMask(15)),this._maskStackHash[r]=i}destroy(){this._renderer=null,this._maskStackHash=null,this._maskHash=null}};F.extension={type:[l.WebGLPipes,l.WebGPUPipes,l.CanvasPipes],name:`stencilMask`};function Ie(e,t){for(let n in e.attributes){let r=e.attributes[n],i=t[n];i?(r.format??=i.format,r.offset??=i.offset,r.instance??=i.instance):b(`Attribute ${n} is not present in the shader, but is present in the geometry. Unable to infer attribute details.`)}Le(e)}function Le(e){let{buffers:t,attributes:n}=e,r={},i={};for(let e in t){let n=t[e];r[n.uid]=0,i[n.uid]=0}for(let e in n){let t=n[e];r[t.buffer.uid]+=fe(t.format).stride}for(let e in n){let t=n[e];t.stride??=r[t.buffer.uid],t.start??=i[t.buffer.uid],i[t.buffer.uid]+=fe(t.format).stride}}var I=[];I[s.NONE]=void 0,I[s.DISABLED]={stencilWriteMask:0,stencilReadMask:0},I[s.RENDERING_MASK_ADD]={stencilFront:{compare:`equal`,passOp:`increment-clamp`},stencilBack:{compare:`equal`,passOp:`increment-clamp`}},I[s.RENDERING_MASK_REMOVE]={stencilFront:{compare:`equal`,passOp:`decrement-clamp`},stencilBack:{compare:`equal`,passOp:`decrement-clamp`}},I[s.MASK_ACTIVE]={stencilWriteMask:0,stencilFront:{compare:`equal`,passOp:`keep`},stencilBack:{compare:`equal`,passOp:`keep`}},I[s.INVERSE_MASK_ACTIVE]={stencilWriteMask:0,stencilFront:{compare:`not-equal`,passOp:`keep`},stencilBack:{compare:`not-equal`,passOp:`keep`}};var Re=class{constructor(e){this._syncFunctionHash=Object.create(null),this._adaptor=e,this._systemCheck()}_systemCheck(){if(!T())throw Error(`Current environment does not allow unsafe-eval, please use pixi.js/unsafe-eval module to enable support.`)}ensureUniformGroup(e){let t=this.getUniformGroupData(e);e.buffer||=new c({data:new Float32Array(t.layout.size/4),usage:d.UNIFORM|d.COPY_DST})}getUniformGroupData(e){return this._syncFunctionHash[e._signature]||this._initUniformGroup(e)}_initUniformGroup(e){let t=e._signature,n=this._syncFunctionHash[t];if(!n){let r=Object.keys(e.uniformStructures).map(t=>e.uniformStructures[t]),i=this._adaptor.createUboElements(r),a=this._generateUboSync(i.uboElements);n=this._syncFunctionHash[t]={layout:i,syncFunction:a}}return this._syncFunctionHash[t]}_generateUboSync(e){return this._adaptor.generateUboSync(e)}syncUniformGroup(e,t,n){let r=this.getUniformGroupData(e);e.buffer||=new c({data:new Float32Array(r.layout.size/4),usage:d.UNIFORM|d.COPY_DST});let i=null;return t||(t=e.buffer.data,i=e.buffer.dataInt32),n||=0,r.syncFunction(e.uniforms,t,i,n),!0}updateUniformGroup(e){if(e.isStatic&&!e._dirtyId)return!1;e._dirtyId=0;let t=this.syncUniformGroup(e);return e.buffer.update(),t}destroy(){this._syncFunctionHash=null}},L=[{type:`mat3x3<f32>`,test:e=>e.value.a!==void 0,ubo:`
            var matrix = uv[name].toArray(true);
            data[offset] = matrix[0];
            data[offset + 1] = matrix[1];
            data[offset + 2] = matrix[2];
            data[offset + 4] = matrix[3];
            data[offset + 5] = matrix[4];
            data[offset + 6] = matrix[5];
            data[offset + 8] = matrix[6];
            data[offset + 9] = matrix[7];
            data[offset + 10] = matrix[8];
        `,uniform:`
            gl.uniformMatrix3fv(ud[name].location, false, uv[name].toArray(true));
        `},{type:`vec4<f32>`,test:e=>e.type===`vec4<f32>`&&e.size===1&&e.value.width!==void 0,ubo:`
            v = uv[name];
            data[offset] = v.x;
            data[offset + 1] = v.y;
            data[offset + 2] = v.width;
            data[offset + 3] = v.height;
        `,uniform:`
            cv = ud[name].value;
            v = uv[name];
            if (cv[0] !== v.x || cv[1] !== v.y || cv[2] !== v.width || cv[3] !== v.height) {
                cv[0] = v.x;
                cv[1] = v.y;
                cv[2] = v.width;
                cv[3] = v.height;
                gl.uniform4f(ud[name].location, v.x, v.y, v.width, v.height);
            }
        `},{type:`vec2<f32>`,test:e=>e.type===`vec2<f32>`&&e.size===1&&e.value.x!==void 0,ubo:`
            v = uv[name];
            data[offset] = v.x;
            data[offset + 1] = v.y;
        `,uniform:`
            cv = ud[name].value;
            v = uv[name];
            if (cv[0] !== v.x || cv[1] !== v.y) {
                cv[0] = v.x;
                cv[1] = v.y;
                gl.uniform2f(ud[name].location, v.x, v.y);
            }
        `},{type:`vec4<f32>`,test:e=>e.type===`vec4<f32>`&&e.size===1&&e.value.red!==void 0,ubo:`
            v = uv[name];
            data[offset] = v.red;
            data[offset + 1] = v.green;
            data[offset + 2] = v.blue;
            data[offset + 3] = v.alpha;
        `,uniform:`
            cv = ud[name].value;
            v = uv[name];
            if (cv[0] !== v.red || cv[1] !== v.green || cv[2] !== v.blue || cv[3] !== v.alpha) {
                cv[0] = v.red;
                cv[1] = v.green;
                cv[2] = v.blue;
                cv[3] = v.alpha;
                gl.uniform4f(ud[name].location, v.red, v.green, v.blue, v.alpha);
            }
        `},{type:`vec3<f32>`,test:e=>e.type===`vec3<f32>`&&e.size===1&&e.value.red!==void 0,ubo:`
            v = uv[name];
            data[offset] = v.red;
            data[offset + 1] = v.green;
            data[offset + 2] = v.blue;
        `,uniform:`
            cv = ud[name].value;
            v = uv[name];
            if (cv[0] !== v.red || cv[1] !== v.green || cv[2] !== v.blue) {
                cv[0] = v.red;
                cv[1] = v.green;
                cv[2] = v.blue;
                gl.uniform3f(ud[name].location, v.red, v.green, v.blue);
            }
        `}];function ze(e,t,n,r){let i=[`
        var v = null;
        var v2 = null;
        var t = 0;
        var index = 0;
        var name = null;
        var arrayOffset = null;
    `],a=0;for(let o=0;o<e.length;o++){let s=e[o],c=s.data.name,l=!1,u=0;for(let e=0;e<L.length;e++)if(L[e].test(s.data)){u=s.offset/4,i.push(`name = "${c}";`,`offset += ${u-a};`,L[e][t]||L[e].ubo),l=!0;break}if(!l)if(s.data.size>1)u=s.offset/4,i.push(n(s,u-a));else{let e=r[s.data.type];u=s.offset/4,i.push(`
                    v = uv.${c};
                    offset += ${u-a};
                    ${e};
                `)}a=u}let o=i.join(`
`);return Function(`uv`,`data`,`dataInt32`,`offset`,o)}function R(e,t){return`
        for (let i = 0; i < ${e*t}; i++) {
            data[offset + (((i / ${e})|0) * 4) + (i % ${e})] = v[i];
        }
    `}var Be={f32:`
        data[offset] = v;`,i32:`
        dataInt32[offset] = v;`,"vec2<f32>":`
        data[offset] = v[0];
        data[offset + 1] = v[1];`,"vec3<f32>":`
        data[offset] = v[0];
        data[offset + 1] = v[1];
        data[offset + 2] = v[2];`,"vec4<f32>":`
        data[offset] = v[0];
        data[offset + 1] = v[1];
        data[offset + 2] = v[2];
        data[offset + 3] = v[3];`,"vec2<i32>":`
        dataInt32[offset] = v[0];
        dataInt32[offset + 1] = v[1];`,"vec3<i32>":`
        dataInt32[offset] = v[0];
        dataInt32[offset + 1] = v[1];
        dataInt32[offset + 2] = v[2];`,"vec4<i32>":`
        dataInt32[offset] = v[0];
        dataInt32[offset + 1] = v[1];
        dataInt32[offset + 2] = v[2];
        dataInt32[offset + 3] = v[3];`,"mat2x2<f32>":`
        data[offset] = v[0];
        data[offset + 1] = v[1];
        data[offset + 4] = v[2];
        data[offset + 5] = v[3];`,"mat3x3<f32>":`
        data[offset] = v[0];
        data[offset + 1] = v[1];
        data[offset + 2] = v[2];
        data[offset + 4] = v[3];
        data[offset + 5] = v[4];
        data[offset + 6] = v[5];
        data[offset + 8] = v[6];
        data[offset + 9] = v[7];
        data[offset + 10] = v[8];`,"mat4x4<f32>":`
        for (let i = 0; i < 16; i++) {
            data[offset + i] = v[i];
        }`,"mat3x2<f32>":R(3,2),"mat4x2<f32>":R(4,2),"mat2x3<f32>":R(2,3),"mat4x3<f32>":R(4,3),"mat2x4<f32>":R(2,4),"mat3x4<f32>":R(3,4)},Ve={...Be,"mat2x2<f32>":`
        data[offset] = v[0];
        data[offset + 1] = v[1];
        data[offset + 2] = v[2];
        data[offset + 3] = v[3];
    `};function He(e,t,n,r,i,a){let o=a?1:-1;return e.identity(),e.a=1/r*2,e.d=o*(1/i*2),e.tx=-1-t*e.a,e.ty=-o-n*e.d,e}var z=new Map;me.register(z);function B(e,t){if(!z.has(e)){let n=new h({source:new p({resource:e,...t})}),r=()=>{z.get(e)===n&&z.delete(e)};n.once(`destroy`,r),n.source.once(`destroy`,r),z.set(e,n)}return z.get(e)}function Ue(e){return z.has(e)}function We(e){let t=e.colorTexture.source.resource;return globalThis.HTMLCanvasElement&&t instanceof HTMLCanvasElement&&document.body.contains(t)}var Ge=class e{constructor(t={}){if(this.uid=o(`renderTarget`),this.colorTextures=[],this.dirtyId=0,this.isRoot=!1,this._size=new Float32Array(2),this._managedColorTextures=!1,t={...e.defaultOptions,...t},this.stencil=t.stencil,this.depth=t.depth,this.isRoot=t.isRoot,typeof t.colorTextures==`number`){this._managedColorTextures=!0;for(let e=0;e<t.colorTextures;e++)this.colorTextures.push(new g({width:t.width,height:t.height,resolution:t.resolution,antialias:t.antialias}))}else{this.colorTextures=[...t.colorTextures.map(e=>e.source)];let e=this.colorTexture.source;this.resize(e.width,e.height,e._resolution)}this.colorTexture.source.on(`resize`,this.onSourceResize,this),(t.depthStencilTexture||this.stencil)&&(t.depthStencilTexture instanceof h||t.depthStencilTexture instanceof g?this.depthStencilTexture=t.depthStencilTexture.source:this.ensureDepthStencilTexture())}get size(){let e=this._size;return e[0]=this.pixelWidth,e[1]=this.pixelHeight,e}get width(){return this.colorTexture.source.width}get height(){return this.colorTexture.source.height}get pixelWidth(){return this.colorTexture.source.pixelWidth}get pixelHeight(){return this.colorTexture.source.pixelHeight}get resolution(){return this.colorTexture.source._resolution}get colorTexture(){return this.colorTextures[0]}onSourceResize(e){this.resize(e.width,e.height,e._resolution,!0)}ensureDepthStencilTexture(){this.depthStencilTexture||=new g({width:this.width,height:this.height,resolution:this.resolution,format:`depth24plus-stencil8`,autoGenerateMipmaps:!1,antialias:!1,mipLevelCount:1})}resize(e,t,n=this.resolution,r=!1){this.dirtyId++,this.colorTextures.forEach((i,a)=>{r&&a===0||i.source.resize(e,t,n)}),this.depthStencilTexture&&this.depthStencilTexture.source.resize(e,t,n)}destroy(){this.colorTexture.source.off(`resize`,this.onSourceResize,this),this._managedColorTextures&&this.colorTextures.forEach(e=>{e.destroy()}),this.depthStencilTexture&&(this.depthStencilTexture.destroy(),delete this.depthStencilTexture)}};Ge.defaultOptions={width:0,height:0,resolution:1,colorTextures:1,stencil:!1,depth:!1,antialias:!1,isRoot:!1};var V=Ge,Ke=class{constructor(e){this.rootViewPort=new i,this.viewport=new i,this.onRenderTargetChange=new D(`onRenderTargetChange`),this.projectionMatrix=new _,this.defaultClearColor=[0,0,0,0],this._renderSurfaceToRenderTargetHash=new Map,this._gpuRenderTargetHash=Object.create(null),this._renderTargetStack=[],this._renderer=e,e.renderableGC.addManagedHash(this,`_gpuRenderTargetHash`)}finishRenderPass(){this.adaptor.finishRenderPass(this.renderTarget)}renderStart({target:e,clear:t,clearColor:n,frame:r}){this._renderTargetStack.length=0,this.push(e,t,n,r),this.rootViewPort.copyFrom(this.viewport),this.rootRenderTarget=this.renderTarget,this.renderingToScreen=We(this.rootRenderTarget),this.adaptor.prerender?.(this.rootRenderTarget)}postrender(){this.adaptor.postrender?.(this.rootRenderTarget)}bind(e,t=!0,n,r){let i=this.getRenderTarget(e),a=this.renderTarget!==i;this.renderTarget=i,this.renderSurface=e;let o=this.getGpuRenderTarget(i);(i.pixelWidth!==o.width||i.pixelHeight!==o.height)&&(this.adaptor.resizeGpuRenderTarget(i),o.width=i.pixelWidth,o.height=i.pixelHeight);let s=i.colorTexture,c=this.viewport,l=s.pixelWidth,u=s.pixelHeight;if(!r&&e instanceof h&&(r=e.frame),r){let e=s._resolution;c.x=r.x*e+.5|0,c.y=r.y*e+.5|0,c.width=r.width*e+.5|0,c.height=r.height*e+.5|0}else c.x=0,c.y=0,c.width=l,c.height=u;return He(this.projectionMatrix,0,0,c.width/s.resolution,c.height/s.resolution,!i.isRoot),this.adaptor.startRenderPass(i,t,n,c),a&&this.onRenderTargetChange.emit(i),i}clear(e,t=E.ALL,n){t&&(e&&=this.getRenderTarget(e),this.adaptor.clear(e||this.renderTarget,t,n,this.viewport))}contextChange(){this._gpuRenderTargetHash=Object.create(null)}push(e,t=E.ALL,n,r){let i=this.bind(e,t,n,r);return this._renderTargetStack.push({renderTarget:i,frame:r}),i}pop(){this._renderTargetStack.pop();let e=this._renderTargetStack[this._renderTargetStack.length-1];this.bind(e.renderTarget,!1,null,e.frame)}getRenderTarget(e){return e.isTexture&&(e=e.source),this._renderSurfaceToRenderTargetHash.get(e)??this._initRenderTarget(e)}copyToTexture(e,t,n,r,i){n.x<0&&(r.width+=n.x,i.x-=n.x,n.x=0),n.y<0&&(r.height+=n.y,i.y-=n.y,n.y=0);let{pixelWidth:a,pixelHeight:o}=e;return r.width=Math.min(r.width,a-n.x),r.height=Math.min(r.height,o-n.y),this.adaptor.copyToTexture(e,t,n,r,i)}ensureDepthStencil(){this.renderTarget.stencil||(this.renderTarget.stencil=!0,this.adaptor.startRenderPass(this.renderTarget,!1,null,this.viewport))}destroy(){this._renderer=null,this._renderSurfaceToRenderTargetHash.forEach((e,t)=>{e!==t&&e.destroy()}),this._renderSurfaceToRenderTargetHash.clear(),this._gpuRenderTargetHash=Object.create(null)}_initRenderTarget(e){let t=null;return p.test(e)&&(e=B(e).source),e instanceof V?t=e:e instanceof g&&(t=new V({colorTextures:[e]}),e.source instanceof p&&(t.isRoot=!0),e.once(`destroy`,()=>{t.destroy(),this._renderSurfaceToRenderTargetHash.delete(e);let n=this._gpuRenderTargetHash[t.uid];n&&(this._gpuRenderTargetHash[t.uid]=null,this.adaptor.destroyGpuRenderTarget(n))})),this._renderSurfaceToRenderTargetHash.set(e,t),t}getGpuRenderTarget(e){return this._gpuRenderTargetHash[e.uid]||(this._gpuRenderTargetHash[e.uid]=this.adaptor.initGpuRenderTarget(e))}resetState(){this.renderTarget=null,this.renderSurface=null}},qe=class extends ee{constructor({buffer:e,offset:t,size:n}){super(),this.uid=o(`buffer`),this._resourceType=`bufferResource`,this._touched=0,this._resourceId=o(`resource`),this._bufferResource=!0,this.destroyed=!1,this.buffer=e,this.offset=t|0,this.size=n,this.buffer.on(`change`,this.onBufferChange,this)}onBufferChange(){this._resourceId=o(`resource`),this.emit(`change`,this)}destroy(e=!1){this.destroyed=!0,e&&this.buffer.destroy(),this.emit(`change`,this),this.buffer=null}},H=class{constructor(e){this._renderer=e}updateRenderable(){}destroyRenderable(){}validateRenderable(){return!1}addRenderable(e,t){this._renderer.renderPipes.batch.break(t),t.add(e)}execute(e){e.isRenderable&&e.render(this._renderer)}destroy(){this._renderer=null}};H.extension={type:[l.WebGLPipes,l.WebGPUPipes,l.CanvasPipes],name:`customRender`};function U(e,t){let n=e.instructionSet,r=n.instructions;for(let e=0;e<n.instructionSize;e++){let n=r[e];t[n.renderPipeId].execute(n)}}var Je=new _,W=class{constructor(e){this._renderer=e}addRenderGroup(e,t){e.isCachedAsTexture?this._addRenderableCacheAsTexture(e,t):this._addRenderableDirect(e,t)}execute(e){e.isRenderable&&(e.isCachedAsTexture?this._executeCacheAsTexture(e):this._executeDirect(e))}destroy(){this._renderer=null}_addRenderableDirect(e,t){this._renderer.renderPipes.batch.break(t),e._batchableRenderGroup&&=(y.return(e._batchableRenderGroup),null),t.add(e)}_addRenderableCacheAsTexture(e,t){let n=e._batchableRenderGroup??=y.get(ge);n.renderable=e.root,n.transform=e.root.relativeGroupTransform,n.texture=e.texture,n.bounds=e._textureBounds,t.add(e),this._renderer.renderPipes.blendMode.pushBlendMode(e,e.root.groupBlendMode,t),this._renderer.renderPipes.batch.addToBatch(n,t),this._renderer.renderPipes.blendMode.popBlendMode(t)}_executeCacheAsTexture(e){if(e.textureNeedsUpdate){e.textureNeedsUpdate=!1;let t=Je.identity().translate(-e._textureBounds.x,-e._textureBounds.y);this._renderer.renderTarget.push(e.texture,!0,null,e.texture.frame),this._renderer.globalUniforms.push({worldTransformMatrix:t,worldColor:4294967295,offset:{x:0,y:0}}),U(e,this._renderer.renderPipes),this._renderer.renderTarget.finishRenderPass(),this._renderer.renderTarget.pop(),this._renderer.globalUniforms.pop()}e._batchableRenderGroup._batcher.updateElement(e._batchableRenderGroup),e._batchableRenderGroup._batcher.geometry.buffers[0].update()}_executeDirect(e){this._renderer.globalUniforms.push({worldTransformMatrix:e.inverseParentTextureTransform,worldColor:e.worldColorAlpha}),U(e,this._renderer.renderPipes),this._renderer.globalUniforms.pop()}};W.extension={type:[l.WebGLPipes,l.WebGPUPipes,l.CanvasPipes],name:`renderGroup`};function G(e,t){t||=0;for(let n=t;n<e.length&&e[n];n++)e[n]=null}var Ye=new a,Xe=7;function K(e,t=!1){Ze(e);let n=e.childrenToUpdate,r=e.updateTick++;for(let t in n){let i=Number(t),a=n[t],o=a.list,s=a.index;for(let t=0;t<s;t++){let n=o[t];n.parentRenderGroup===e&&n.relativeRenderGroupDepth===i&&q(n,r,0)}G(o,s),a.index=0}if(t)for(let n=0;n<e.renderGroupChildren.length;n++)K(e.renderGroupChildren[n],t)}function Ze(e){let t=e.root,n;if(e.renderGroupParent){let r=e.renderGroupParent;e.worldTransform.appendFrom(t.relativeGroupTransform,r.worldTransform),e.worldColor=se(t.groupColor,r.worldColor),n=t.groupAlpha*r.worldAlpha}else e.worldTransform.copyFrom(t.localTransform),e.worldColor=t.localColor,n=t.localAlpha;n=n<0?0:n>1?1:n,e.worldAlpha=n,e.worldColorAlpha=e.worldColor+((n*255|0)<<24)}function q(e,t,n){if(t===e.updateTick)return;e.updateTick=t,e.didChange=!1;let r=e.localTransform;e.updateLocalTransform();let i=e.parent;if(i&&!i.renderGroup?(n|=e._updateFlags,e.relativeGroupTransform.appendFrom(r,i.relativeGroupTransform),n&Xe&&Qe(e,i,n)):(n=e._updateFlags,e.relativeGroupTransform.copyFrom(r),n&Xe&&Qe(e,Ye,n)),!e.renderGroup){let r=e.children,i=r.length;for(let e=0;e<i;e++)q(r[e],t,n);let a=e.parentRenderGroup,o=e;o.renderPipeId&&!a.structureDidChange&&a.updateRenderable(o)}}function Qe(e,t,n){if(n&1){e.groupColor=se(e.localColor,t.groupColor);let n=e.localAlpha*t.groupAlpha;n=n<0?0:n>1?1:n,e.groupAlpha=n,e.groupColorAlpha=e.groupColor+((n*255|0)<<24)}n&2&&(e.groupBlendMode=e.localBlendMode===`inherit`?t.groupBlendMode:e.localBlendMode),n&4&&(e.globalDisplayStatus=e.localDisplayStatus&t.globalDisplayStatus),e._updateFlags=0}function $e(e,t){let{list:n,index:r}=e.childrenRenderablesToUpdate,i=!1;for(let e=0;e<r;e++){let r=n[e];if(i=t[r.renderPipeId].validateRenderable(r),i)break}return e.structureDidChange=i,i}var et=new _,J=class{constructor(e){this._renderer=e}render({container:e,transform:t}){let n=e.parent,r=e.renderGroup.renderGroupParent;e.parent=null,e.renderGroup.renderGroupParent=null;let i=this._renderer,a=et;t&&(a.copyFrom(e.renderGroup.localTransform),e.renderGroup.localTransform.copyFrom(t));let o=i.renderPipes;this._updateCachedRenderGroups(e.renderGroup,null),this._updateRenderGroups(e.renderGroup),i.globalUniforms.start({worldTransformMatrix:t?e.renderGroup.localTransform:e.renderGroup.worldTransform,worldColor:e.renderGroup.worldColorAlpha}),U(e.renderGroup,o),o.uniformBatch&&o.uniformBatch.renderEnd(),t&&e.renderGroup.localTransform.copyFrom(a),e.parent=n,e.renderGroup.renderGroupParent=r}destroy(){this._renderer=null}_updateCachedRenderGroups(e,n){if(e._parentCacheAsTextureRenderGroup=n,e.isCachedAsTexture){if(!e.textureNeedsUpdate)return;n=e}for(let t=e.renderGroupChildren.length-1;t>=0;t--)this._updateCachedRenderGroups(e.renderGroupChildren[t],n);if(e.invalidateMatrices(),e.isCachedAsTexture){if(e.textureNeedsUpdate){let n=e.root.getLocalBounds();n.ceil();let r=e.texture;e.texture&&t.returnTexture(e.texture,!0);let i=this._renderer,a=e.textureOptions.resolution||i.view.resolution,o=e.textureOptions.antialias??i.view.antialias,s=e.textureOptions.scaleMode??`linear`,c=t.getOptimalTexture(n.width,n.height,a,o);c._source.style=new ue({scaleMode:s}),e.texture=c,e._textureBounds||=new x,e._textureBounds.copyFrom(n),r!==e.texture&&e.renderGroupParent&&(e.renderGroupParent.structureDidChange=!0)}}else e.texture&&=(t.returnTexture(e.texture,!0),null)}_updateRenderGroups(e){let t=this._renderer,n=t.renderPipes;if(e.runOnRender(t),e.instructionSet.renderPipes=n,e.structureDidChange?G(e.childrenRenderablesToUpdate.list,0):$e(e,n),K(e),e.structureDidChange?(e.structureDidChange=!1,this._buildInstructions(e,t)):this._updateRenderables(e),e.childrenRenderablesToUpdate.index=0,t.renderPipes.batch.upload(e.instructionSet),!(e.isCachedAsTexture&&!e.textureNeedsUpdate))for(let t=0;t<e.renderGroupChildren.length;t++)this._updateRenderGroups(e.renderGroupChildren[t])}_updateRenderables(e){let{list:t,index:n}=e.childrenRenderablesToUpdate;for(let r=0;r<n;r++){let n=t[r];n.didViewUpdate&&e.updateRenderable(n)}G(t,n)}_buildInstructions(e,t){let n=e.root,r=e.instructionSet;r.reset();let i=t.renderPipes?t:t.batch.renderer,a=i.renderPipes;a.batch.buildStart(r),a.blendMode.buildStart(),a.colorMask.buildStart(),n.sortableChildren&&n.sortChildren(),n.collectRenderablesWithEffects(r,i,null),a.batch.buildEnd(r),a.blendMode.buildEnd(r)}};J.extension={type:[l.WebGLSystem,l.WebGPUSystem,l.CanvasSystem],name:`renderGroup`};var Y=class{constructor(e){this._renderer=e}addRenderable(e,t){let n=this._getGpuSprite(e);e.didViewUpdate&&this._updateBatchableSprite(e,n),this._renderer.renderPipes.batch.addToBatch(n,t)}updateRenderable(e){let t=this._getGpuSprite(e);e.didViewUpdate&&this._updateBatchableSprite(e,t),t._batcher.updateElement(t)}validateRenderable(e){let t=this._getGpuSprite(e);return!t._batcher.checkAndUpdateTexture(t,e._texture)}_updateBatchableSprite(e,t){t.bounds=e.visualBounds,t.texture=e._texture}_getGpuSprite(e){return e._gpuData[this._renderer.uid]||this._initGPUSprite(e)}_initGPUSprite(e){let t=new ge;return t.renderable=e,t.transform=e.groupTransform,t.texture=e._texture,t.bounds=e.visualBounds,t.roundPixels=this._renderer._roundPixels|e._roundPixels,e._gpuData[this._renderer.uid]=t,t}destroy(){this._renderer=null}};Y.extension={type:[l.WebGLPipes,l.WebGPUPipes,l.CanvasPipes],name:`sprite`};var X=class e{constructor(){this.clearBeforeRender=!0,this._backgroundColor=new v(0),this.color=this._backgroundColor,this.alpha=1}init(t){t={...e.defaultOptions,...t},this.clearBeforeRender=t.clearBeforeRender,this.color=t.background||t.backgroundColor||this._backgroundColor,this.alpha=t.backgroundAlpha,this._backgroundColor.setAlpha(t.backgroundAlpha)}get color(){return this._backgroundColor}set color(e){v.shared.setValue(e).alpha<1&&this._backgroundColor.alpha===1&&b(`Cannot set a transparent background on an opaque canvas. To enable transparency, set backgroundAlpha < 1 when initializing your Application.`),this._backgroundColor.setValue(e)}get alpha(){return this._backgroundColor.alpha}set alpha(e){this._backgroundColor.setAlpha(e)}get colorRgba(){return this._backgroundColor.toArray()}destroy(){}};X.extension={type:[l.WebGLSystem,l.WebGPUSystem,l.CanvasSystem],name:`background`,priority:0},X.defaultOptions={backgroundAlpha:1,backgroundColor:0,clearBeforeRender:!0};var tt=X,Z={};f.handle(l.BlendMode,e=>{if(!e.name)throw Error(`BlendMode extension must have a name property`);Z[e.name]=e.ref},e=>{delete Z[e.name]});var nt=class{constructor(e){this._blendModeStack=[],this._isAdvanced=!1,this._filterHash=Object.create(null),this._renderer=e,this._renderer.runners.prerender.add(this)}prerender(){this._activeBlendMode=`normal`,this._isAdvanced=!1}pushBlendMode(e,t,n){this._blendModeStack.push(t),this.setBlendMode(e,t,n)}popBlendMode(e){this._blendModeStack.pop();let t=this._blendModeStack[this._activeBlendMode.length-1]??`normal`;this.setBlendMode(null,t,e)}setBlendMode(e,t,n){let r=e instanceof de;if(this._activeBlendMode===t){this._isAdvanced&&e&&!r&&this._renderableList?.push(e);return}this._isAdvanced&&this._endAdvancedBlendMode(n),this._activeBlendMode=t,e&&(this._isAdvanced=!!Z[t],this._isAdvanced&&this._beginAdvancedBlendMode(e,n))}_beginAdvancedBlendMode(e,t){this._renderer.renderPipes.batch.break(t);let n=this._activeBlendMode;if(!Z[n]){b(`Unable to assign BlendMode: '${n}'. You may want to include: import 'pixi.js/advanced-blend-modes'`);return}let r=this._ensureFilterEffect(n),i=e instanceof de,a={renderPipeId:`filter`,action:`pushFilter`,filterEffect:r,renderables:i?null:[e],container:i?e.root:null,canBundle:!1};this._renderableList=a.renderables,t.add(a)}_ensureFilterEffect(e){let t=this._filterHash[e];return t||(t=this._filterHash[e]=new ne,t.filters=[new Z[e]]),t}_endAdvancedBlendMode(e){this._isAdvanced=!1,this._renderableList=null,this._renderer.renderPipes.batch.break(e),e.add({renderPipeId:`filter`,action:`popFilter`,canBundle:!1})}buildStart(){this._isAdvanced=!1}buildEnd(e){this._isAdvanced&&this._endAdvancedBlendMode(e)}destroy(){this._renderer=null,this._renderableList=null;for(let e in this._filterHash)this._filterHash[e].destroy();this._filterHash=null}};nt.extension={type:[l.WebGLPipes,l.WebGPUPipes,l.CanvasPipes],name:`blendMode`};var rt={png:`image/png`,jpg:`image/jpeg`,webp:`image/webp`},it=class e{constructor(e){this._renderer=e}_normalizeOptions(e,t={}){return e instanceof a||e instanceof h?{target:e,...t}:{...t,...e}}async image(e){let t=r.get().createImage();return t.src=await this.base64(e),t}async base64(t){t=this._normalizeOptions(t,e.defaultImageOptions);let{format:n,quality:r}=t,i=this.canvas(t);if(i.toBlob!==void 0)return new Promise((e,t)=>{i.toBlob(n=>{if(!n){t(Error(`ICanvas.toBlob failed!`));return}let r=new FileReader;r.onload=()=>e(r.result),r.onerror=t,r.readAsDataURL(n)},rt[n],r)});if(i.toDataURL!==void 0)return i.toDataURL(rt[n],r);if(i.convertToBlob!==void 0){let e=await i.convertToBlob({type:rt[n],quality:r});return new Promise((t,n)=>{let r=new FileReader;r.onload=()=>t(r.result),r.onerror=n,r.readAsDataURL(e)})}throw Error(`Extract.base64() requires ICanvas.toDataURL, ICanvas.toBlob, or ICanvas.convertToBlob to be implemented`)}canvas(e){e=this._normalizeOptions(e);let t=e.target,n=this._renderer;if(t instanceof h)return n.texture.generateCanvas(t);let r=n.textureGenerator.generateTexture(e),i=n.texture.generateCanvas(r);return r.destroy(!0),i}pixels(e){e=this._normalizeOptions(e);let t=e.target,n=this._renderer,r=t instanceof h?t:n.textureGenerator.generateTexture(e),i=n.texture.getPixels(r);return t instanceof a&&r.destroy(!0),i}texture(e){return e=this._normalizeOptions(e),e.target instanceof h?e.target:this._renderer.textureGenerator.generateTexture(e)}download(e){e=this._normalizeOptions(e);let t=this.canvas(e),n=document.createElement(`a`);n.download=e.filename??`image.png`,n.href=t.toDataURL(`image/png`),document.body.appendChild(n),n.click(),document.body.removeChild(n)}log(e){let t=e.width??200;e=this._normalizeOptions(e);let n=this.canvas(e),r=n.toDataURL();console.log(`[Pixi Texture] ${n.width}px ${n.height}px`);let i=[`font-size: 1px;`,`padding: ${t}px 300px;`,`background: url(${r}) no-repeat;`,`background-size: contain;`].join(` `);console.log(`%c `,i)}destroy(){this._renderer=null}};it.extension={type:[l.WebGLSystem,l.WebGPUSystem],name:`extract`},it.defaultImageOptions={format:`png`,quality:1};var at=it,ot=class e extends h{static create(t){return new e({source:new g(t)})}resize(e,t,n){return this.source.resize(e,t,n),this}},st=new i,ct=new x,lt=[0,0,0,0],ut=class{constructor(e){this._renderer=e}generateTexture(e){e instanceof a&&(e={target:e,frame:void 0,textureSourceOptions:{},resolution:void 0});let t=e.resolution||this._renderer.resolution,n=e.antialias||this._renderer.view.antialias,r=e.target,i=e.clearColor;i=i?Array.isArray(i)&&i.length===4?i:v.shared.setValue(i).toArray():lt;let o=e.frame?.copyTo(st)||re(r,ct).rectangle;o.width=Math.max(o.width,1/t)|0,o.height=Math.max(o.height,1/t)|0;let s=ot.create({...e.textureSourceOptions,width:o.width,height:o.height,resolution:t,antialias:n}),c=_.shared.translate(-o.x,-o.y);return this._renderer.render({container:r,transform:c,target:s,clearColor:i}),s.source.updateMipmaps(),s}destroy(){this._renderer=null}};ut.extension={type:[l.WebGLSystem,l.WebGPUSystem],name:`textureGenerator`};var dt=class{constructor(e){this._stackIndex=0,this._globalUniformDataStack=[],this._uniformsPool=[],this._activeUniforms=[],this._bindGroupPool=[],this._activeBindGroups=[],this._renderer=e}reset(){this._stackIndex=0;for(let e=0;e<this._activeUniforms.length;e++)this._uniformsPool.push(this._activeUniforms[e]);for(let e=0;e<this._activeBindGroups.length;e++)this._bindGroupPool.push(this._activeBindGroups[e]);this._activeUniforms.length=0,this._activeBindGroups.length=0}start(e){this.reset(),this.push(e)}bind({size:t,projectionMatrix:n,worldTransformMatrix:r,worldColor:i,offset:a}){let o=this._renderer.renderTarget.renderTarget,s=this._stackIndex?this._globalUniformDataStack[this._stackIndex-1]:{projectionData:o,worldTransformMatrix:new _,worldColor:4294967295,offset:new e},c={projectionMatrix:n||this._renderer.renderTarget.projectionMatrix,resolution:t||o.size,worldTransformMatrix:r||s.worldTransformMatrix,worldColor:i||s.worldColor,offset:a||s.offset,bindGroup:null},l=this._uniformsPool.pop()||this._createUniforms();this._activeUniforms.push(l);let u=l.uniforms;u.uProjectionMatrix=c.projectionMatrix,u.uResolution=c.resolution,u.uWorldTransformMatrix.copyFrom(c.worldTransformMatrix),u.uWorldTransformMatrix.tx-=c.offset.x,u.uWorldTransformMatrix.ty-=c.offset.y,ve(c.worldColor,u.uWorldColorAlpha,0),l.update();let d;this._renderer.renderPipes.uniformBatch?d=this._renderer.renderPipes.uniformBatch.getUniformBindGroup(l,!1):(d=this._bindGroupPool.pop()||new le,this._activeBindGroups.push(d),d.setResource(l,0)),c.bindGroup=d,this._currentGlobalUniformData=c}push(e){this.bind(e),this._globalUniformDataStack[this._stackIndex++]=this._currentGlobalUniformData}pop(){this._currentGlobalUniformData=this._globalUniformDataStack[--this._stackIndex-1],this._renderer.type===m.WEBGL&&this._currentGlobalUniformData.bindGroup.resources[0].update()}get bindGroup(){return this._currentGlobalUniformData.bindGroup}get globalUniformData(){return this._currentGlobalUniformData}get uniformGroup(){return this._currentGlobalUniformData.bindGroup.resources[0]}_createUniforms(){return new te({uProjectionMatrix:{value:new _,type:`mat3x3<f32>`},uWorldTransformMatrix:{value:new _,type:`mat3x3<f32>`},uWorldColorAlpha:{value:new Float32Array(4),type:`vec4<f32>`},uResolution:{value:[0,0],type:`vec2<f32>`}},{isStatic:!0})}destroy(){this._renderer=null,this._globalUniformDataStack.length=0,this._uniformsPool.length=0,this._activeUniforms.length=0,this._bindGroupPool.length=0,this._activeBindGroups.length=0,this._currentGlobalUniformData=null}};dt.extension={type:[l.WebGLSystem,l.WebGPUSystem,l.CanvasSystem],name:`globalUniforms`};var ft=1,pt=class{constructor(){this._tasks=[],this._offset=0}init(){be.system.add(this._update,this)}repeat(e,t,n=!0){let r=ft++,i=0;return n&&(this._offset+=1e3,i=this._offset),this._tasks.push({func:e,duration:t,start:performance.now(),offset:i,last:performance.now(),repeat:!0,id:r}),r}cancel(e){for(let t=0;t<this._tasks.length;t++)if(this._tasks[t].id===e){this._tasks.splice(t,1);return}}_update(){let e=performance.now();for(let t=0;t<this._tasks.length;t++){let n=this._tasks[t];if(e-n.offset-n.last>=n.duration){let t=e-n.start;n.func(t),n.last=e}}}destroy(){be.system.remove(this._update,this),this._tasks.length=0}};pt.extension={type:[l.WebGLSystem,l.WebGPUSystem,l.CanvasSystem],name:`scheduler`,priority:0};var mt=!1;function ht(e){if(!mt){if(r.get().getNavigator().userAgent.toLowerCase().indexOf(`chrome`)>-1){let t=[`%c  %c  %c  %c  %c PixiJS %c v${O} (${e}) http://www.pixijs.com/

`,`background: #E72264; padding:5px 0;`,`background: #6CA2EA; padding:5px 0;`,`background: #B5D33D; padding:5px 0;`,`background: #FED23F; padding:5px 0;`,`color: #FFFFFF; background: #E72264; padding:5px 0;`,`color: #E72264; background: #FFFFFF; padding:5px 0;`];globalThis.console.log(...t)}else globalThis.console&&globalThis.console.log(`PixiJS ${O} - ${e} - http://www.pixijs.com/`);mt=!0}}var Q=class{constructor(e){this._renderer=e}init(e){if(e.hello){let e=this._renderer.name;this._renderer.type===m.WEBGL&&(e+=` ${this._renderer.context.webGLVersion}`),ht(e)}}};Q.extension={type:[l.WebGLSystem,l.WebGPUSystem,l.CanvasSystem],name:`hello`,priority:-2},Q.defaultOptions={hello:!1};function gt(e){let t=!1;for(let n in e)if(e[n]==null){t=!0;break}if(!t)return e;let n=Object.create(null);for(let t in e){let r=e[t];r&&(n[t]=r)}return n}function _t(e){let t=0;for(let n=0;n<e.length;n++)e[n]==null?t++:e[n-t]=e[n];return e.length-=t,e}var vt=0,yt=class e{constructor(e){this._managedRenderables=[],this._managedHashes=[],this._managedArrays=[],this._renderer=e}init(t){t={...e.defaultOptions,...t},this.maxUnusedTime=t.renderableGCMaxUnusedTime,this._frequency=t.renderableGCFrequency,this.enabled=t.renderableGCActive}get enabled(){return!!this._handler}set enabled(e){this.enabled!==e&&(e?(this._handler=this._renderer.scheduler.repeat(()=>this.run(),this._frequency,!1),this._hashHandler=this._renderer.scheduler.repeat(()=>{for(let e of this._managedHashes)e.context[e.hash]=gt(e.context[e.hash])},this._frequency),this._arrayHandler=this._renderer.scheduler.repeat(()=>{for(let e of this._managedArrays)_t(e.context[e.hash])},this._frequency)):(this._renderer.scheduler.cancel(this._handler),this._renderer.scheduler.cancel(this._hashHandler),this._renderer.scheduler.cancel(this._arrayHandler)))}addManagedHash(e,t){this._managedHashes.push({context:e,hash:t})}addManagedArray(e,t){this._managedArrays.push({context:e,hash:t})}prerender({container:e}){this._now=performance.now(),e.renderGroup.gcTick=vt++,this._updateInstructionGCTick(e.renderGroup,e.renderGroup.gcTick)}addRenderable(e){this.enabled&&(e._lastUsed===-1&&(this._managedRenderables.push(e),e.once(`destroyed`,this._removeRenderable,this)),e._lastUsed=this._now)}run(){let e=this._now,t=this._managedRenderables,n=this._renderer.renderPipes,r=0;for(let i=0;i<t.length;i++){let a=t[i];if(a===null){r++;continue}let o=a.renderGroup??a.parentRenderGroup,s=o?.instructionSet?.gcTick??-1;if((o?.gcTick??0)===s&&(a._lastUsed=e),e-a._lastUsed>this.maxUnusedTime){if(!a.destroyed){let e=n;o&&(o.structureDidChange=!0),e[a.renderPipeId].destroyRenderable(a)}a._lastUsed=-1,r++,a.off(`destroyed`,this._removeRenderable,this)}else t[i-r]=a}t.length-=r}destroy(){this.enabled=!1,this._renderer=null,this._managedRenderables.length=0,this._managedHashes.length=0,this._managedArrays.length=0}_removeRenderable(e){let t=this._managedRenderables.indexOf(e);t>=0&&(e.off(`destroyed`,this._removeRenderable,this),this._managedRenderables[t]=null)}_updateInstructionGCTick(e,t){e.instructionSet.gcTick=t;for(let n of e.renderGroupChildren)this._updateInstructionGCTick(n,t)}};yt.extension={type:[l.WebGLSystem,l.WebGPUSystem],name:`renderableGC`,priority:0},yt.defaultOptions={renderableGCActive:!0,renderableGCMaxUnusedTime:6e4,renderableGCFrequency:3e4};var bt=yt,xt=class e{constructor(e){this._renderer=e,this.count=0,this.checkCount=0}init(t){t={...e.defaultOptions,...t},this.checkCountMax=t.textureGCCheckCountMax,this.maxIdle=t.textureGCAMaxIdle??t.textureGCMaxIdle,this.active=t.textureGCActive}postrender(){this._renderer.renderingToScreen&&(this.count++,this.active&&(this.checkCount++,this.checkCount>this.checkCountMax&&(this.checkCount=0,this.run())))}run(){let e=this._renderer.texture.managedTextures;for(let t=0;t<e.length;t++){let n=e[t];n.autoGarbageCollect&&n.resource&&n._touched>-1&&this.count-n._touched>this.maxIdle&&(n._touched=-1,n.unload())}}destroy(){this._renderer=null}};xt.extension={type:[l.WebGLSystem,l.WebGPUSystem],name:`textureGC`},xt.defaultOptions={textureGCActive:!0,textureGCAMaxIdle:null,textureGCMaxIdle:3600,textureGCCheckCountMax:600};var St=xt,$=class e{get autoDensity(){return this.texture.source.autoDensity}set autoDensity(e){this.texture.source.autoDensity=e}get resolution(){return this.texture.source._resolution}set resolution(e){this.texture.source.resize(this.texture.source.width,this.texture.source.height,e)}init(t){t={...e.defaultOptions,...t},t.view&&(ie(n,`ViewSystem.view has been renamed to ViewSystem.canvas`),t.canvas=t.view),this.screen=new i(0,0,t.width,t.height),this.canvas=t.canvas||r.get().createCanvas(),this.antialias=!!t.antialias,this.texture=B(this.canvas,t),this.renderTarget=new V({colorTextures:[this.texture],depth:!!t.depth,isRoot:!0}),this.texture.source.transparent=t.backgroundAlpha<1,this.resolution=t.resolution}resize(e,t,n){this.texture.source.resize(e,t,n),this.screen.width=this.texture.frame.width,this.screen.height=this.texture.frame.height}destroy(e=!1){(typeof e==`boolean`?e:e?.removeView)&&this.canvas.parentNode&&this.canvas.parentNode.removeChild(this.canvas),this.texture.destroy()}};$.extension={type:[l.WebGLSystem,l.WebGPUSystem,l.CanvasSystem],name:`view`,priority:0},$.defaultOptions={width:800,height:600,autoDensity:!1,antialias:!1};var Ct=$,wt=[tt,dt,Q,Ct,J,St,ut,at,k,bt,pt],Tt=[nt,M,Y,W,N,F,P,H];export{De as $,We as A,Ie as B,G as C,qe as D,H as E,Ve as F,Ne as G,P as H,ze as I,A as J,M as K,L,Ue as M,He as N,Ke as O,Be as P,k as Q,Re as R,q as S,U as T,N as U,F as V,Me as W,ke as X,Ae as Y,Oe as Z,Y as _,bt as a,Ce as at,Ze as b,Q as c,dt as d,O as et,ut as f,tt as g,nt as h,St as i,T as it,B as j,V as k,ht as l,at as m,wt as n,D as nt,_t as o,C as ot,ot as p,je as q,Ct as r,E as rt,gt as s,Se as st,Tt as t,Ee as tt,pt as u,J as v,W as w,K as x,$e as y,I as z};