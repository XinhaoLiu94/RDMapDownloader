"""共享山体效果的光照与场景恢复回归，不依赖网络或 Node.js。"""

import json
import os
from pathlib import Path
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtQml import QJSEngine
from PyQt5.QtWidgets import QApplication

BOOTSTRAP = r"""
var window = this;
function Vector(x, y, z) { this.x=x||0; this.y=y||0; this.z=z||0; }
Vector.fromRadians = function(lon, lat) {
  return new Vector(Math.cos(lat)*Math.cos(lon), Math.cos(lat)*Math.sin(lon), Math.sin(lat));
};
Vector.fromDegrees = function(lon, lat) { return Vector.fromRadians(lon*Math.PI/180, lat*Math.PI/180); };
Vector.normalize = function(v, out) {
  var length=Math.sqrt(v.x*v.x+v.y*v.y+v.z*v.z);
  out.x=v.x/length; out.y=v.y/length; out.z=v.z/length; return out;
};
var Cesium = {
  Cartesian3: Vector,
  Math: {toRadians:function(degrees) { return degrees*Math.PI/180; }},
  DirectionalLight: function(options) { this.direction=options.direction; this.intensity=options.intensity; },
  HeadingPitchRange: function(heading, pitch, range) { this.heading=heading; this.pitch=pitch; this.range=range; },
  SkyAtmosphere: function() { this.show=false; this.destroy=function() { this.destroyed=true; }; },
  Transforms: {eastNorthUpToFixedFrame:function(point) {
    var lon=Math.atan2(point.y, point.x), lat=Math.atan2(point.z, Math.sqrt(point.x*point.x+point.y*point.y));
    return [
      new Vector(-Math.sin(lon), Math.cos(lon), 0),
      new Vector(-Math.sin(lat)*Math.cos(lon), -Math.sin(lat)*Math.sin(lon), Math.cos(lat)),
      Vector.fromRadians(lon, lat)
    ];
  }},
  Matrix4: {IDENTITY:{}, multiplyByPointAsVector:function(frame, value, out) {
    out.x=frame[0].x*value.x+frame[1].x*value.y+frame[2].x*value.z;
    out.y=frame[0].y*value.x+frame[1].y*value.y+frame[2].y*value.z;
    out.z=frame[0].z*value.x+frame[1].z*value.y+frame[2].z*value.z;
    return out;
  }}
};
function makeViewer() {
  return {
    camera: {
      positionCartographic: {longitude:0, latitude:0},
      moves:0,
      lookAt:function() { this.moves++; },
      lookAtTransform:function() { this.moves++; }
    },
    clock: {currentTime:123456, shouldAnimate:true},
    scene: {
      renders:0, requestRender:function() { this.renders++; },
      light:{original:true}, verticalExaggeration:1,
      globe:{terrainExaggeration:1, enableLighting:false, showGroundAtmosphere:false,
        dynamicAtmosphereLighting:false, dynamicAtmosphereLightingFromSun:true,
        lightingFadeOutDistance:100, lightingFadeInDistance:200},
      skyBox:{show:true}, sun:{show:true}, moon:{show:true}, fog:{enabled:false,density:0.01}
    }
  };
}
var viewer = makeViewer();
"""


class TerrainVisualTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.source = (
            Path(__file__).resolve().parents[1] / "assets/cesium/terrain-visual.js"
        ).read_text(encoding="utf-8")

    def setUp(self):
        self.engine = QJSEngine()
        self.evaluate(BOOTSTRAP)
        self.evaluate(self.source)

    def evaluate(self, script):
        result = self.engine.evaluate(script)
        if result.isError():
            self.fail(result.toString())
        return result.toVariant()

    def test_light_is_above_the_horizon_worldwide_without_changing_the_clock(self):
        for longitude, latitude in (
            (108.9, 33.7),
            (-112.1, 36.1),
            (-70, -33),
            (179, 70),
            (-179, -70),
            (0, 90),
            (120, -90),
        ):
            with self.subTest(longitude=longitude, latitude=latitude):
                result = self.evaluate("""
                    var point=Cesium.Cartesian3.fromDegrees(%s,%s);
                    RDMapTerrainVisual.restore(viewer,{active:true,exaggeration:2},{target:point});
                    var light=viewer.scene.light.direction;
                    ({illumination:-(light.x*point.x+light.y*point.y+light.z*point.z),
                      time:viewer.clock.currentTime,animate:viewer.clock.shouldAnimate,
                      moves:viewer.camera.moves});
                """ % (longitude, latitude))
                self.assertGreater(result["illumination"], 0.5)
                self.assertEqual(result["time"], 123456)
                self.assertTrue(result["animate"])
                self.assertEqual(result["moves"], 0)
                self.evaluate("RDMapTerrainVisual.reset(viewer)")

    def test_saved_effects_restore_without_moving_the_saved_camera(self):
        self.evaluate("RDMapTerrainVisual.apply(viewer,{exaggeration:2.7})")
        saved = self.evaluate("RDMapTerrainVisual.snapshot()")
        self.evaluate("RDMapTerrainVisual.reset(viewer); viewer.camera.moves=0;")
        self.evaluate("RDMapTerrainVisual.restore(viewer," + json.dumps(saved) + ")")
        restored = self.evaluate("""({saved:RDMapTerrainVisual.snapshot(),
            exaggeration:viewer.scene.verticalExaggeration,
            lighting:viewer.scene.globe.enableLighting,moves:viewer.camera.moves})""")
        self.assertEqual(restored["saved"], {"active": True, "exaggeration": 2.7})
        self.assertEqual(restored["exaggeration"], 2.7)
        self.assertTrue(restored["lighting"])
        self.assertEqual(restored["moves"], 0)

    def test_active_light_tracks_a_move_across_hemispheres(self):
        self.evaluate(
            "var originalLight=viewer.scene.light; RDMapTerrainVisual.restore(viewer,{active:true,exaggeration:2},{target:Cesium.Cartesian3.fromDegrees(110,34)});"
        )
        illumination = self.evaluate("""
            var target=Cesium.Cartesian3.fromDegrees(-112,36);
            RDMapTerrainVisual.updateLight(viewer,target);
            var light=viewer.scene.light.direction;
            -(light.x*target.x+light.y*target.y+light.z*target.z);
        """)
        self.assertGreater(illumination, 0.5)
        self.assertEqual(self.evaluate("viewer.camera.moves"), 0)
        self.evaluate(
            "RDMapTerrainVisual.reset(viewer); RDMapTerrainVisual.updateLight(viewer,Cesium.Cartesian3.fromDegrees(0,0));"
        )
        self.assertTrue(self.evaluate("viewer.scene.light===originalLight"))

    def test_reset_restores_original_scene_and_keeps_slider_preference(self):
        self.evaluate(
            "var originalLight=viewer.scene.light; RDMapTerrainVisual.apply(viewer,{exaggeration:3}); var atmosphere=viewer.scene.skyAtmosphere; RDMapTerrainVisual.reset(viewer);"
        )
        restored = self.evaluate(
            """({sameLight:viewer.scene.light===originalLight,
            exaggeration:viewer.scene.verticalExaggeration,lighting:viewer.scene.globe.enableLighting,
            fog:viewer.scene.fog,skyBox:viewer.scene.skyBox.show,sun:viewer.scene.sun.show,
            atmosphereAbsent:viewer.scene.skyAtmosphere===undefined,
            atmosphereDestroyed:atmosphere.destroyed,preference:RDMapTerrainVisual.snapshot()})"""
        )
        self.assertTrue(restored["sameLight"])
        self.assertEqual(restored["exaggeration"], 1)
        self.assertFalse(restored["lighting"])
        self.assertEqual(restored["fog"], {"enabled": False, "density": 0.01})
        self.assertTrue(restored["skyBox"])
        self.assertTrue(restored["sun"])
        self.assertTrue(restored["atmosphereAbsent"])
        self.assertTrue(restored["atmosphereDestroyed"])
        self.assertEqual(restored["preference"], {"active": False, "exaggeration": 3})

    def test_inactive_preference_and_legacy_state_are_safe(self):
        self.evaluate(
            "RDMapTerrainVisual.restore(viewer,{active:false,exaggeration:2.6})"
        )
        self.assertEqual(self.evaluate("viewer.scene.verticalExaggeration"), 1)
        self.assertEqual(
            self.evaluate("RDMapTerrainVisual.snapshot().exaggeration"), 2.6
        )
        for value in ("undefined", "null", "{}", "{active:'yes'}"):
            self.evaluate("RDMapTerrainVisual.restore(viewer," + value + ")")
        self.assertEqual(
            self.evaluate("RDMapTerrainVisual.snapshot()"),
            {"active": False, "exaggeration": 2.6},
        )
        self.evaluate(
            "RDMapTerrainVisual.restore(viewer,{active:true,exaggeration:99})"
        )
        self.assertEqual(self.evaluate("viewer.scene.verticalExaggeration"), 3)
        self.evaluate("RDMapTerrainVisual.setExaggeration(viewer,-2)")
        self.assertEqual(self.evaluate("viewer.scene.verticalExaggeration"), 1)


if __name__ == "__main__":
    unittest.main()
