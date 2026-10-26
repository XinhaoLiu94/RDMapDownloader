(function () {
  'use strict';

  // Keep the slider preference even while the terrain effects are disabled.
  var state = { active: false, exaggeration: 2 };
  var original = null;

  function amount(value) {
    var number = Number(value);
    return isFinite(number) ? Math.max(1, Math.min(3, number)) : 2;
  }

  function writeExaggeration(viewer, value) {
    viewer.scene.verticalExaggeration = value;
    var globe = viewer.scene.globe;
    if ('terrainExaggeration' in globe) globe.terrainExaggeration = value;
  }

  function setExaggeration(viewer, value) {
    state.exaggeration = amount(value);
    if (state.active) writeExaggeration(viewer, state.exaggeration);
    viewer.scene.requestRender();
    return snapshot();
  }

  function targetPoint(viewer, target) {
    if (target) return target;
    var carto = viewer.camera.positionCartographic;
    return Cesium.Cartesian3.fromRadians(carto.longitude, carto.latitude, 0);
  }

  function localLight(viewer, target) {
    // Follow the visible region rather than keeping one ECEF direction when
    // the camera moves to the other side of the globe.
    var frame = Cesium.Transforms.eastNorthUpToFixedFrame(targetPoint(viewer, target));
    var direction = Cesium.Matrix4.multiplyByPointAsVector(
      frame, new Cesium.Cartesian3(0.4, -0.4, -0.7), new Cesium.Cartesian3());
    Cesium.Cartesian3.normalize(direction, direction);
    return new Cesium.DirectionalLight({ direction: direction, intensity: 2 });
  }

  function updateLight(viewer, target) {
    if (!state.active) return;
    viewer.scene.light = localLight(viewer, target);
    viewer.scene.requestRender();
  }

  function enableEffects(viewer, target) {
    var scene = viewer.scene;
    var globe = scene.globe;
    if (!original) {
      original = {
        light: scene.light,
        exaggeration: scene.verticalExaggeration,
        terrainExaggeration: globe.terrainExaggeration,
        lighting: globe.enableLighting,
        groundAtmosphere: globe.showGroundAtmosphere,
        dynamicAtmosphere: globe.dynamicAtmosphereLighting,
        atmosphereFromSun: globe.dynamicAtmosphereLightingFromSun,
        fadeOut: globe.lightingFadeOutDistance,
        fadeIn: globe.lightingFadeInDistance,
        skyAtmosphere: scene.skyAtmosphere,
        skyAtmosphereShow: scene.skyAtmosphere && scene.skyAtmosphere.show,
        skyBoxShow: scene.skyBox && scene.skyBox.show,
        sunShow: scene.sun && scene.sun.show,
        moonShow: scene.moon && scene.moon.show,
        fogEnabled: scene.fog && scene.fog.enabled,
        fogDensity: scene.fog && scene.fog.density
      };
    }

    // A local light keeps the terrain readable at every longitude and latitude,
    // including polar regions. It does not depend on the current UTC time.
    scene.light = localLight(viewer, target);

    globe.enableLighting = true;
    globe.showGroundAtmosphere = true;
    globe.dynamicAtmosphereLighting = true;
    globe.dynamicAtmosphereLightingFromSun = false;
    globe.lightingFadeOutDistance = 1e9;
    globe.lightingFadeInDistance = 1e9 + 1;

    if (scene.skyBox) scene.skyBox.show = false;
    if (!scene.skyAtmosphere) {
      try { scene.skyAtmosphere = new Cesium.SkyAtmosphere(); } catch (reason) {}
    }
    if (scene.skyAtmosphere) scene.skyAtmosphere.show = true;
    // The sun sprite follows astronomical time, not the local display light.
    if (scene.sun) scene.sun.show = false;
    if (scene.moon) scene.moon.show = false;
    if (scene.fog) {
      scene.fog.enabled = true;
      scene.fog.density = 0.00004;
    }

    state.active = true;
    writeExaggeration(viewer, state.exaggeration);
  }

  function apply(viewer, options) {
    options = options || {};
    state.exaggeration = amount(options.exaggeration === undefined ? state.exaggeration : options.exaggeration);
    var target = targetPoint(viewer, options.target);
    enableEffects(viewer, target);
    var range = Number(options.range) || 80000;
    var pitch = isFinite(options.pitch) ? options.pitch : Cesium.Math.toRadians(-16);
    var heading = isFinite(options.heading) ? options.heading : 0;
    viewer.camera.lookAt(
      target,
      new Cesium.HeadingPitchRange(heading, pitch, range)
    );
    viewer.camera.lookAtTransform(Cesium.Matrix4.IDENTITY);

    viewer.scene.requestRender();
    return snapshot();
  }

  function reset(viewer) {
    var scene = viewer.scene;
    var globe = scene.globe;
    if (original) {
      scene.light = original.light;
      scene.verticalExaggeration = original.exaggeration;
      if ('terrainExaggeration' in globe) globe.terrainExaggeration = original.terrainExaggeration;
      globe.enableLighting = original.lighting;
      globe.showGroundAtmosphere = original.groundAtmosphere;
      globe.dynamicAtmosphereLighting = original.dynamicAtmosphere;
      globe.dynamicAtmosphereLightingFromSun = original.atmosphereFromSun;
      globe.lightingFadeOutDistance = original.fadeOut;
      globe.lightingFadeInDistance = original.fadeIn;
      var atmosphere = scene.skyAtmosphere;
      scene.skyAtmosphere = original.skyAtmosphere;
      if (atmosphere && atmosphere !== original.skyAtmosphere && atmosphere.destroy) atmosphere.destroy();
      if (scene.skyAtmosphere) scene.skyAtmosphere.show = original.skyAtmosphereShow;
      if (scene.skyBox) scene.skyBox.show = original.skyBoxShow;
      if (scene.sun) scene.sun.show = original.sunShow;
      if (scene.moon) scene.moon.show = original.moonShow;
      if (scene.fog) {
        scene.fog.enabled = original.fogEnabled;
        scene.fog.density = original.fogDensity;
      }
      original = null;
    }
    state.active = false;
    scene.requestRender();
    return snapshot();
  }

  function snapshot() {
    return { active: state.active, exaggeration: state.exaggeration };
  }

  function restore(viewer, saved, options) {
    if (!saved || typeof saved.active !== 'boolean') return snapshot();
    state.exaggeration = amount(saved.exaggeration);
    if (saved.active) enableEffects(viewer, options && options.target);
    else reset(viewer);
    viewer.scene.requestRender();
    return snapshot();
  }

  window.RDMapTerrainVisual = {
    apply: apply,
    reset: reset,
    setExaggeration: setExaggeration,
    snapshot: snapshot,
    restore: restore,
    updateLight: updateLight,
    state: state
  };
})();
