// Offline transport stub only: never imports camera hardware or the production store.
export function createFixtureCameraSource({ref,reactive}){
 // handDetection / setHandDetection 必须存在：跟随面板在并行准备相机之后要
 // 判断"检测是否已经开着"，没开就单独把推理拉起来，不再重走一遍开相机。
 return {browser:ref(false),browserPreview:{state:reactive({status:'idle',frameReady:false})},busy:ref(false),message:ref(''),handDetection:ref(false),setHandDetection:async()=>true,prepareHandFollow:async()=>true}
}
