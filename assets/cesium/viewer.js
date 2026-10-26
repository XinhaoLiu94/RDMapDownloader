(function(){
  'use strict';
  var bridge,viewer,sourceKey='',selectionEntity,firstPoint,firstMarker,drawing=false,enabled=true,homeBounds,selectionBounds=null,activeRequests=0;
  var scheme=new Cesium.WebMercatorTilingScheme();
  function message(text){if(bridge)bridge.message(text);}
  function error(error){if(bridge)bridge.error(String(error&&error.message||error));}
  window.addEventListener('error',function(event){error(event.error||event.message);});
  window.addEventListener('unhandledrejection',function(event){error(event.reason);});
  function terrainProvider(){
    var errorEvent=new Cesium.Event();
    var zeroError=Cesium.TerrainProvider.getEstimatedLevelZeroGeometricErrorForAHeightmap(Cesium.Ellipsoid.WGS84,65,1);
    return {
      tilingScheme:scheme,errorEvent:errorEvent,credit:new Cesium.Credit('高程：Mapzen Skadi / USGS SRTM 等 · EGM96 → WGS84 椭球高'),
      hasWaterMask:false,hasVertexNormals:false,ready:true,readyPromise:Promise.resolve(true),availability:undefined,
      getLevelMaximumGeometricError:function(level){return zeroError/Math.pow(2,level);},
      getTileDataAvailable:function(x,y,level){return level<=15;},loadTileDataAvailability:function(){},
      requestTileGeometry:function(x,y,level){
        if(activeRequests>=4)return undefined;
        function make(buffer){return new Cesium.HeightmapTerrainData({buffer:buffer,width:65,height:65,childTileMask:level<15?15:0});}
        if(level<9)return Promise.resolve(make(new Float32Array(65*65)));
        activeRequests++;
        return fetch('terrain/'+level+'/'+x+'/'+y).then(function(response){if(!response.ok)throw new Error('高程暂不可用');return response.arrayBuffer();})
          .then(function(buffer){if(buffer.byteLength!==65*65*4)throw new Error('高程数据尺寸错误');bridge.terrainLoaded();return make(new Float32Array(buffer));})
          .catch(function(reason){errorEvent.raiseEvent({message:String(reason),timesRetried:0,retry:false});throw reason;})
          .finally(function(){activeRequests--;});
      }
    };
  }
  function boundsArray(rectangle){return [Math.min(85.0511287798,Cesium.Math.toDegrees(rectangle.north)),Math.max(-85.0511287798,Cesium.Math.toDegrees(rectangle.south)),Cesium.Math.toDegrees(rectangle.west),Cesium.Math.toDegrees(rectangle.east)];}
  function visibleBounds(){
    var points=[],width=viewer.canvas.clientWidth-1,height=viewer.canvas.clientHeight-1;
    function add(x,y){var p=picked(new Cesium.Cartesian2(x,y));if(p)points.push([Cesium.Math.toDegrees(p.latitude),Cesium.Math.toDegrees(p.longitude)]);}
    for(var index=0;index<=4;index++){var t=index/4;add(t*width,0);add(t*width,height);add(0,t*height);add(width,t*height);}
    add(width/2,height/2);
    if(points.length>=3){var lat=points.map(function(p){return p[0];}),lon=points.map(function(p){return p[1];});var bounds=[Math.min(85.0511287798,Math.max.apply(null,lat)),Math.max(-85.0511287798,Math.min.apply(null,lat)),Math.min.apply(null,lon),Math.max.apply(null,lon)];if(bounds[3]-bounds[2]>180)return null;return bounds;}
    var rect=viewer.camera.computeViewRectangle(Cesium.Ellipsoid.WGS84);return rect?boundsArray(rect):null;
  }
  function picked(position){var ray=viewer.camera.getPickRay(position);if(!ray)return null;var cartesian=viewer.scene.globe.pick(ray,viewer.scene);return cartesian?Cesium.Cartographic.fromCartesian(cartesian):null;}
  function cameraState(){
    var camera=viewer.camera,p=camera.positionWC;
    var centre=picked(new Cesium.Cartesian2(viewer.canvas.clientWidth/2,viewer.canvas.clientHeight/2))||camera.positionCartographic;
    return {position:[p.x,p.y,p.z],heading:camera.heading,pitch:camera.pitch,roll:camera.roll,bounds:visibleBounds(),homeBounds:homeBounds?homeBounds.slice():null,centre:[Cesium.Math.toDegrees(centre.latitude),Cesium.Math.toDegrees(centre.longitude)],height:camera.positionCartographic.height,terrainVisual:RDMapTerrainVisual.snapshot()};
  }
  function coordinateText(lat,lon){return '经度 '+(lon>=0?'+':'')+lon.toFixed(6)+'　纬度 '+(lat>=0?'+':'')+lat.toFixed(6);}
  function reportCamera(){if(bridge&&viewer){var state=cameraState();RDMapTerrainVisual.updateLight(viewer,Cesium.Cartesian3.fromDegrees(state.centre[1],state.centre[0],0));document.getElementById('coordinates').textContent=coordinateText(state.centre[0],state.centre[1]);bridge.camera(JSON.stringify(state));}}
  function endDrawing(){drawing=false;firstPoint=null;document.getElementById('drawing').hidden=true;if(firstMarker){viewer.entities.remove(firstMarker);firstMarker=null;}viewer.canvas.style.cursor='';if(bridge)bridge.selectionMode(false);}
  function setSelection(bounds){
    selectionBounds=bounds||null;
    if(selectionEntity){viewer.entities.remove(selectionEntity);selectionEntity=null;}
    if(bounds&&bounds[0]>bounds[1]&&bounds[3]>bounds[2]){
      var c=Cesium.Color.fromCssColorString(getComputedStyle(document.documentElement).getPropertyValue('--accent').trim());
      selectionEntity=viewer.entities.add({rectangle:{coordinates:Cesium.Rectangle.fromDegrees(bounds[2],bounds[1],bounds[3],bounds[0]),material:c.withAlpha(.12)},polyline:{positions:Cesium.Cartesian3.fromDegreesArray([bounds[2],bounds[0],bounds[3],bounds[0],bounds[3],bounds[1],bounds[2],bounds[1],bounds[2],bounds[0]]),width:2,material:c,clampToGround:true}});
    }
    viewer.scene.requestRender();
  }
  function terrainTarget(){
    var lat,lon;
    if(selectionBounds){lat=(selectionBounds[0]+selectionBounds[1])/2;lon=(selectionBounds[2]+selectionBounds[3])/2;}
    else{var c=viewer.camera.positionCartographic;lat=Cesium.Math.toDegrees(c.latitude);lon=Cesium.Math.toDegrees(c.longitude);}
    return Cesium.Cartesian3.fromDegrees(lon,lat,0);
  }  function terrainRange(){
    var range=80000;
    if(selectionBounds){var span=Math.max(selectionBounds[0]-selectionBounds[1],selectionBounds[3]-selectionBounds[2]);range=Math.min(120000,Math.max(50000,span*111000));}
    return range;
  }
  function syncTerrainControls(){
    var visual=RDMapTerrainVisual.snapshot(),button=document.getElementById('terrain');
    button.classList.toggle('active',visual.active);button.textContent=visual.active?'恢复俯视':'山体视角';
    document.getElementById('exaggeration').value=String(visual.exaggeration);
    document.getElementById('exagValue').textContent=visual.exaggeration.toFixed(1)+'×';
  }
  function applyTerrainView(){
    var slider=document.getElementById('exaggeration');
    var exaggeration=parseFloat(slider.value)||2;
    RDMapTerrainVisual.apply(viewer,{target:terrainTarget(),range:terrainRange(),exaggeration:exaggeration});
    syncTerrainControls();
    reportCamera();
  }
  function resetTerrainView(){
    RDMapTerrainVisual.reset(viewer);
    syncTerrainControls();
    var bounds=selectionBounds||homeBounds||visibleBounds();
    var orientation={heading:0,pitch:-Math.PI/2,roll:0};
    if(bounds&&bounds[0]>bounds[1]&&bounds[3]>bounds[2])viewer.camera.flyTo({destination:Cesium.Rectangle.fromDegrees(bounds[2],bounds[1],bounds[3],bounds[0]),orientation:orientation,duration:0});
    else viewer.camera.setView({orientation:orientation});
    viewer.scene.requestRender();
    reportCamera();
  }
  window.rdmap={
    setSource:function(data){
      if(sourceKey===data.key)return;sourceKey=data.key;
      viewer.imageryLayers.removeAll();
      var provider=new Cesium.UrlTemplateImageryProvider({url:new URL('imagery/'+data.key+'/{z}/{x}/{y}',location.href).href.replace(/%7B/g,'{').replace(/%7D/g,'}'),tilingScheme:scheme,minimumLevel:0,maximumLevel:data.maxZoom,credit:data.credit});
      provider.errorEvent.addEventListener(function(){message('影像加载失败，请检查网络或切换图源。');});
      viewer.imageryLayers.addImageryProvider(provider);viewer.scene.requestRender();
    },
    flyTo:function(bounds){if(!bounds)return;homeBounds=bounds;viewer.camera.flyTo({destination:Cesium.Rectangle.fromDegrees(bounds[2],bounds[1],bounds[3],bounds[0]),duration:0});reportCamera();},
    restoreCamera:function(state){
      if(!state||!state.position||state.position.length!==3)return;
      var savedHome=state.homeBounds||state.bounds;
      if(savedHome&&savedHome.length===4&&savedHome[0]>savedHome[1]&&savedHome[3]>savedHome[2])homeBounds=savedHome.slice();
      if(state.terrainVisual){
        var target=state.centre&&state.centre.length===2?Cesium.Cartesian3.fromDegrees(state.centre[1],state.centre[0],0):new Cesium.Cartesian3(state.position[0],state.position[1],state.position[2]);
        RDMapTerrainVisual.restore(viewer,state.terrainVisual,{target:target});syncTerrainControls();
      }
      viewer.camera.setView({destination:new Cesium.Cartesian3(state.position[0],state.position[1],state.position[2]),orientation:{heading:state.heading,pitch:state.pitch,roll:state.roll}});reportCamera();
    },
    setSelection:setSelection,
    beginSelection:function(){if(!enabled)return;endDrawing();drawing=true;viewer.canvas.style.cursor='crosshair';document.getElementById('drawing').hidden=false;bridge.selectionMode(true);},
    cancelSelection:endDrawing,
    setEnabled:function(value){enabled=value;if(!enabled)endDrawing();},
    useCurrentView:function(){if(!enabled)return;endDrawing();var bounds=visibleBounds();if(!bounds||bounds[2]>=bounds[3]){message('当前视野跨越日界线或朝向天空，请缩小范围后重试。');return;}setSelection(bounds);bridge.selection(JSON.stringify(bounds));},
    setActive:function(value){if(!value)endDrawing();viewer.useDefaultRenderLoop=value;if(value)viewer.scene.requestRender();},
    terrainView:function(){if(RDMapTerrainVisual.state.active)resetTerrainView();else applyTerrainView();return RDMapTerrainVisual.state;},
    resetTerrainView:resetTerrainView,
    setExaggeration:function(value){RDMapTerrainVisual.setExaggeration(viewer,value);syncTerrainControls();reportCamera();return RDMapTerrainVisual.snapshot();},
    setTheme:function(colors){var style=document.documentElement.style;style.setProperty('--surface',colors.surface);style.setProperty('--text',colors.on_surface);style.setProperty('--muted',colors.muted);style.setProperty('--accent',colors.primary);style.setProperty('--border',colors.outline);viewer.scene.backgroundColor=Cesium.Color.fromCssColorString(colors.surface);viewer.scene.requestRender();},
    snapshot:cameraState,
    diagnostics:function(){var surface=viewer.scene.globe._surface;return {frames:viewer.scene._frameState.frameNumber,loop:viewer.useDefaultRenderLoop,show:viewer.scene.globe.show,tiles:surface._tilesToRender.length,levels:surface._tilesToRender.map(function(tile){return tile.level;}),high:surface._tileLoadQueueHigh.length,medium:surface._tileLoadQueueMedium.length,low:surface._tileLoadQueueLow.length,requests:activeRequests};}
  };
  function start(channel){
    bridge=channel.objects.bridge;
    try{
      Cesium.Ion.defaultAccessToken='';
      Cesium.RequestScheduler.maximumRequestsPerServer=6;
      viewer=new Cesium.Viewer('globe',{baseLayer:false,terrainProvider:terrainProvider(),animation:false,timeline:false,baseLayerPicker:false,geocoder:false,homeButton:false,sceneModePicker:false,navigationHelpButton:false,fullscreenButton:false,infoBox:false,selectionIndicator:false,scene3DOnly:true,requestRenderMode:true,skyBox:false,skyAtmosphere:false,contextOptions:{webgl:{preserveDrawingBuffer:true}}});
      window.__viewer=viewer;
      viewer.scene.globe.maximumScreenSpaceError=2;
      viewer.scene.globe.tileCacheSize=100;
      viewer.scene.renderError.addEventListener(function(scene,reason){error(reason);});
      viewer.camera.moveEnd.addEventListener(reportCamera);
      viewer.screenSpaceEventHandler.removeInputAction(Cesium.ScreenSpaceEventType.LEFT_DOUBLE_CLICK);
      var handler=new Cesium.ScreenSpaceEventHandler(viewer.canvas),lastPointer=0;
      handler.setInputAction(function(event){
        if(!drawing||!enabled)return;var p=picked(event.position);if(!p)return;
        var lat=Cesium.Math.toDegrees(p.latitude),lon=Cesium.Math.toDegrees(p.longitude);
        if(!firstPoint){firstPoint=[lat,lon];firstMarker=viewer.entities.add({position:Cesium.Cartesian3.fromRadians(p.longitude,p.latitude,p.height),point:{pixelSize:7,color:Cesium.Color.YELLOW,disableDepthTestDistance:Number.POSITIVE_INFINITY}});document.getElementById('drawing').textContent='点击另一个对角点，Esc 取消';}
        else{var first=firstPoint.slice();endDrawing();if(Math.abs(first[1]-lon)>180){message('请分开选择日界线两侧的区域。');return;}var bounds=[Math.max(first[0],lat),Math.min(first[0],lat),Math.min(first[1],lon),Math.max(first[1],lon)];setSelection(bounds);bridge.selection(JSON.stringify(bounds));}
      },Cesium.ScreenSpaceEventType.LEFT_CLICK);
      handler.setInputAction(function(event){if(Date.now()-lastPointer<80)return;lastPointer=Date.now();var p=picked(event.endPosition);if(p){var lat=Cesium.Math.toDegrees(p.latitude),lon=Cesium.Math.toDegrees(p.longitude);document.getElementById('coordinates').textContent=coordinateText(lat,lon);bridge.position(lat,lon);}},Cesium.ScreenSpaceEventType.MOUSE_MOVE);
      document.getElementById('reset').onclick=function(){if(RDMapTerrainVisual.state.active)resetTerrainView();else if(homeBounds)window.rdmap.flyTo(homeBounds);};
      document.getElementById('north').onclick=function(){viewer.camera.setView({orientation:{heading:0,pitch:viewer.camera.pitch,roll:0}});reportCamera();};
      document.getElementById('tilt').onclick=function(){var camera=viewer.camera,p=picked(new Cesium.Cartesian2(viewer.canvas.clientWidth/2,viewer.canvas.clientHeight/2));if(!p)return;var centre=Cesium.Cartesian3.fromRadians(p.longitude,p.latitude,p.height);var range=Cesium.Cartesian3.distance(camera.positionWC,centre);var tilted=camera.pitch>-1.2;camera.lookAt(centre,new Cesium.HeadingPitchRange(camera.heading,tilted?-Math.PI/2:-Math.PI/4,range));camera.lookAtTransform(Cesium.Matrix4.IDENTITY);this.textContent=tilted?'倾斜视角':'俯视视角';viewer.scene.requestRender();reportCamera();};
      document.getElementById('plus').onclick=function(){viewer.camera.zoomIn(Math.max(50,viewer.camera.positionCartographic.height*.35));viewer.scene.requestRender();reportCamera();};
      document.getElementById('minus').onclick=function(){viewer.camera.zoomOut(Math.max(50,viewer.camera.positionCartographic.height*.35));viewer.scene.requestRender();reportCamera();};
      document.getElementById('terrain').onclick=function(){window.rdmap.terrainView();};
      document.getElementById('exaggeration').oninput=function(){window.rdmap.setExaggeration(parseFloat(this.value)||1);};
      document.addEventListener('keydown',function(event){if(event.key==='Escape')endDrawing();});
      bridge.ready();
    }catch(reason){error(reason);}
  }
  if(window.qt&&window.QWebChannel)new QWebChannel(qt.webChannelTransport,start);
})();
