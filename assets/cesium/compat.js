// 当前固定的 Cesium 构建兼容 Qt 5 Chromium；保留标准 API 补充以支持 Qt 小版本差异。
if (!Array.prototype.at) Object.defineProperty(Array.prototype,'at',{configurable:true,writable:true,value:function(index){var n=Math.trunc(Number(index)||0),length=this.length;if(n<0)n+=length;return n<0||n>=length?undefined:this[n];}});
if (!Array.prototype.toSorted) Object.defineProperty(Array.prototype,'toSorted',{configurable:true,writable:true,value:function(compare){return Array.from(this).sort(compare);}});
if (!Object.hasOwn) Object.defineProperty(Object,'hasOwn',{configurable:true,writable:true,value:function(object,key){return Object.prototype.hasOwnProperty.call(object,key);}});
