# 前端 bundle 分析结果（可复现）

- 入口：https://aistudio.tencent.com/
- 标题：Hy AI Studio
- /api 路径总数：135
- 是否出现 api.hunyuan.tencent.com：**False**

## bundle 概览

| bundle | 大小 | /api 路径数 | 值得注意的关键字 |
| --- | --- | --- | --- |
| 6473__39850b1ae564af0fd214.js | 8024001 | 1 | {'EventSource': 25, 'WebSocket': 54, 'getReader': 2, 'ReadableStream': 6, 'signature': 11, 'nonce': 9, 'timestamp': 11, 'TmpSecretId': 2} |
| cos-sdk__dcbf0ed4dec6fec14296.js | 321957 | 0 | {'signature': 4, 'timestamp': 2, 'TmpSecretId': 10} |
| hy-web__e62837fa9ff77f96314a.js | 7617749 | 8 | {'signature': 6, 'nonce': 19, 'timestamp': 6} |
| index__70332c7932348066f545.js | 3861196 | 126 | {'WebSocket': 7, 'getReader': 7, 'ReadableStream': 10, 'signature': 19, 'nonce': 7, 'timestamp': 20, 'TmpSecretId': 13} |
| monitor__35b65e6889c75bdde006.js | 201665 | 0 | {'WebSocket': 12, 'timestamp': 7} |
| react__c5a2d4fdc3acccbe93d0.js | 240002 | 0 | - |
| runtime__af3a211516436687e0d1.js | 10153 | 0 | {'nonce': 1} |
| tdesign-icons__00b8e35db39fda858289.js | 1512797 | 0 | - |
| tdesign__9dc349d2a51371011bc6.js | 870896 | 0 | - |
| utils__731e71f93fbdc6dcbbb9.js | 353433 | 0 | {'getReader': 2, 'ReadableStream': 16} |

## 每个 bundle 的关键字计数

### 6473__39850b1ae564af0fd214.js

- `text/event-stream`：0
- `EventSource`：25
- `WebSocket`：54
- `getReader`：2
- `ReadableStream`：6
- `TextDecoder`：19
- `onprogress`：0
- `ndjson`：0
- `credentials:"include"`：0
- `Authorization`：2
- `signature`：11
- `nonce`：9
- `timestamp`：11
- `TmpSecretId`：2
- `new-portal/chat`：0
- `new-portal/stop`：0

### cos-sdk__dcbf0ed4dec6fec14296.js

- `text/event-stream`：0
- `EventSource`：0
- `WebSocket`：0
- `getReader`：0
- `ReadableStream`：0
- `TextDecoder`：0
- `onprogress`：4
- `ndjson`：0
- `credentials:"include"`：0
- `Authorization`：46
- `signature`：4
- `nonce`：0
- `timestamp`：2
- `TmpSecretId`：10
- `new-portal/chat`：0
- `new-portal/stop`：0

### hy-web__e62837fa9ff77f96314a.js

- `text/event-stream`：0
- `EventSource`：0
- `WebSocket`：0
- `getReader`：0
- `ReadableStream`：0
- `TextDecoder`：8
- `onprogress`：6
- `ndjson`：0
- `credentials:"include"`：3
- `Authorization`：0
- `signature`：6
- `nonce`：19
- `timestamp`：6
- `TmpSecretId`：0
- `new-portal/chat`：3
- `new-portal/stop`：0

### index__70332c7932348066f545.js

- `text/event-stream`：0
- `EventSource`：0
- `WebSocket`：7
- `getReader`：7
- `ReadableStream`：10
- `TextDecoder`：4
- `onprogress`：1
- `ndjson`：0
- `credentials:"include"`：1
- `Authorization`：9
- `signature`：19
- `nonce`：7
- `timestamp`：20
- `TmpSecretId`：13
- `new-portal/chat`：13
- `new-portal/stop`：1

### monitor__35b65e6889c75bdde006.js

- `text/event-stream`：0
- `EventSource`：0
- `WebSocket`：12
- `getReader`：0
- `ReadableStream`：0
- `TextDecoder`：0
- `onprogress`：0
- `ndjson`：0
- `credentials:"include"`：0
- `Authorization`：0
- `signature`：0
- `nonce`：0
- `timestamp`：7
- `TmpSecretId`：0
- `new-portal/chat`：0
- `new-portal/stop`：0

### react__c5a2d4fdc3acccbe93d0.js

- `text/event-stream`：0
- `EventSource`：0
- `WebSocket`：0
- `getReader`：0
- `ReadableStream`：0
- `TextDecoder`：0
- `onprogress`：0
- `ndjson`：0
- `credentials:"include"`：0
- `Authorization`：0
- `signature`：0
- `nonce`：0
- `timestamp`：0
- `TmpSecretId`：0
- `new-portal/chat`：0
- `new-portal/stop`：0

### runtime__af3a211516436687e0d1.js

- `text/event-stream`：0
- `EventSource`：0
- `WebSocket`：0
- `getReader`：0
- `ReadableStream`：0
- `TextDecoder`：0
- `onprogress`：0
- `ndjson`：0
- `credentials:"include"`：0
- `Authorization`：0
- `signature`：0
- `nonce`：1
- `timestamp`：0
- `TmpSecretId`：0
- `new-portal/chat`：0
- `new-portal/stop`：0

### tdesign-icons__00b8e35db39fda858289.js

- `text/event-stream`：0
- `EventSource`：0
- `WebSocket`：0
- `getReader`：0
- `ReadableStream`：0
- `TextDecoder`：0
- `onprogress`：0
- `ndjson`：0
- `credentials:"include"`：0
- `Authorization`：0
- `signature`：0
- `nonce`：0
- `timestamp`：0
- `TmpSecretId`：0
- `new-portal/chat`：0
- `new-portal/stop`：0

### tdesign__9dc349d2a51371011bc6.js

- `text/event-stream`：0
- `EventSource`：0
- `WebSocket`：0
- `getReader`：0
- `ReadableStream`：0
- `TextDecoder`：0
- `onprogress`：1
- `ndjson`：0
- `credentials:"include"`：0
- `Authorization`：0
- `signature`：0
- `nonce`：0
- `timestamp`：0
- `TmpSecretId`：0
- `new-portal/chat`：0
- `new-portal/stop`：0

### utils__731e71f93fbdc6dcbbb9.js

- `text/event-stream`：0
- `EventSource`：0
- `WebSocket`：0
- `getReader`：2
- `ReadableStream`：16
- `TextDecoder`：0
- `onprogress`：0
- `ndjson`：0
- `credentials:"include"`：0
- `Authorization`：4
- `signature`：0
- `nonce`：0
- `timestamp`：0
- `TmpSecretId`：0
- `new-portal/chat`：0
- `new-portal/stop`：0


## source map 检查（任务书 §13）

- `runtime__af3a211516436687e0d1.js.map` -> 404
- `react__c5a2d4fdc3acccbe93d0.js.map` -> 404
- `monitor__35b65e6889c75bdde006.js.map` -> 404
- `utils__731e71f93fbdc6dcbbb9.js.map` -> 404
- `tdesign-icons__00b8e35db39fda858289.js.map` -> 404
- `tdesign__9dc349d2a51371011bc6.js.map` -> 404
- `cos-sdk__dcbf0ed4dec6fec14296.js.map` -> 404
- `hy-web__e62837fa9ff77f96314a.js.map` -> 404
- `6473__39850b1ae564af0fd214.js.map` -> 404
- `index__70332c7932348066f545.js.map` -> 404

## 全部 /api 路径

```text
/api/account/certify/getverificationcode
/api/account/certify/upsert
/api/account/confirmRestoreUser
/api/account/unregister
/api/admin/auditOpenAPI/token/models/databoard
/api/admin/auditOpenAPI/token/models/linechart
/api/agent/model/list
/api/aigc/v1/datareport/zhiyanmonitor/report
/api/auditOpenAPI/lifetime/modelDetail
/api/auth-center/oauth2/authorize
/api/backend/audit0penAPI/folders/list
/api/drag-source-monitor
/api/drop-target-monitor
/api/findergetobjecturl
/api/getZhiyanEnv
/api/getuserinfo
/api/image/clarity
/api/image/edit/asset/save
/api/image/elimination
/api/image/outpainting
/api/image/removewatermark
/api/image/resource/review
/api/image/search
/api/image/style
/api/info/general
/api/inputguide/agentSkills
/api/jointappid
/api/js
/api/models
/api/new-portal/account/bind
/api/new-portal/account/tmpToken
/api/new-portal/aiRoleClone/characterDetail
/api/new-portal/aiRoleClone/realtime
/api/new-portal/aiRoleClone/systemCharacterList
/api/new-portal/asr/chatinput/recognize
/api/new-portal/asr/file/queryTask
/api/new-portal/asr/file/recognize
/api/new-portal/asr/history/del
/api/new-portal/asr/history/list
/api/new-portal/asr/realtime/recognize
/api/new-portal/audio/synthesis
/api/new-portal/case/list
/api/new-portal/chat
/api/new-portal/chat/agent/detail
/api/new-portal/chat/conversation/clear
/api/new-portal/chat/conversation/history/del
/api/new-portal/chat/conversation/share
/api/new-portal/chat/conversation/update
/api/new-portal/chat/repeat
/api/new-portal/chat/resource/download
/api/new-portal/chat/resource/genUploadInfo
/api/new-portal/chat/resource/v1/download
/api/new-portal/config
/api/new-portal/feedback/complaint
/api/new-portal/feedback/suitable
/api/new-portal/generate/id
/api/new-portal/getAgentConfig
/api/new-portal/getChatConfig
/api/new-portal/getuserinfo
/api/new-portal/image/search
/api/new-portal/interview/cloneDetail
/api/new-portal/interview/publishClone
/api/new-portal/jointappid
/api/new-portal/jump/feedback
/api/new-portal/login/email/getverificationcode
/api/new-portal/login/email/login
/api/new-portal/login/getverificationcode
/api/new-portal/login/getverificationcodeauthen
/api/new-portal/login/joint
/api/new-portal/login/logout
/api/new-portal/login/phonelogin
/api/new-portal/podcast/hint
/api/new-portal/podcast/interact
/api/new-portal/podcast/relInfo
/api/new-portal/register/joint
/api/new-portal/stop/chat
/api/new-portal/tempCred
/api/new-portal/user/agent/conversation/continue
/api/new-portal/user/agent/conversation/v1/detail
/api/new-portal/visionGenerationConfig
/api/new-portal/whitelist/apply
/api/new-portal/whitelist/query
/api/new-portal/whitelist/queryList
/api/new-portal/wxapp/service/getSign
/api/oalogin
/api/objects
/api/resource/download
/api/resource/fileParse
/api/test
/api/third_plugin/developer/gen_upload_info
/api/third_plugin/list
/api/url/check/
/api/use-drag
/api/user/agent/conversation/textcheck
/api/user/agent/conversation/updateModel
/api/user/agent/quotainfo
/api/vision_platform/apikey/common
/api/vision_platform/audio/tts
/api/vision_platform/auditOpenAPI/folders/list
/api/vision_platform/auditOpenAPI/model/detail
/api/vision_platform/auditOpenAPI/model/list
/api/vision_platform/auditOpenAPI/model_set/detail
/api/vision_platform/auditOpenAPI/model_set/list
/api/vision_platform/auth/config
/api/vision_platform/auth/permission/list
/api/vision_platform/auth/role/list
/api/vision_platform/auth/space/create
/api/vision_platform/auth/space/del
/api/vision_platform/auth/space/imageAudit
/api/vision_platform/auth/space/info
/api/vision_platform/auth/space/list
/api/vision_platform/auth/space/transfer
/api/vision_platform/auth/space/update
/api/vision_platform/auth/user/acceptInvite
/api/vision_platform/auth/user/changeRole
/api/vision_platform/auth/user/invite
/api/vision_platform/auth/user/inviteInfo
/api/vision_platform/auth/user/list
/api/vision_platform/auth/user/quit
/api/vision_platform/auth/user/remove
/api/vision_platform/auth/user/stopInvite
/api/vision_platform/game/resource/genUploadInfo
/api/vision_platform/generate/cid
/api/vision_platform/generation
/api/vision_platform/getZhiyanEnv
/api/vision_platform/oalogin
/api/vision_platform/ocr
/api/vision_platform/public/auditOpenAPI/folders/count
/api/vision_platform/public/auditOpenAPI/model/list
/api/vision_platform/public/auditOpenAPI/model/modelTabInfo
/api/vision_platform/query_task
/api/vision_platform/query_task_list
/api/vision_platform/resource/genUploadInfo
/api/vision_platform/taskHistoryDel
/api/weixin/get_sign
```

## 全部主机

```text
http://a
http://docs.oasis-open.org
http://example.com
http://fb.me
http://file
http://localhost
http://macVmlSchemaUri
http://mozilla.github.io
http://ns.adobe.com
http://openoffice.org
http://predata.tab.sparta.html5.qq.com
http://purl.oclc.org
http://purl.org
http://react-dnd.github.io
http://schemas.microsoft.com
http://schemas.openxmlformats.org
http://sheetjs.openxmlformats.org
http://url.com
http://www.eclipse.org
http://www.ibm.com
http://www.mozilla.org
http://www.plantuml.com
http://www.w3.org
http://www.xfa.org
http://x
https://36kr.com
https://3d-models.hunyuan.tencent.com
https://3d-test.hunyuan.tencent.com
https://a
https://aegis.qq.com
https://aigc-material-prod-1258344703.cos.ap-guangzhou.myqcloud.com
https://aistudio.tencent.com
https://aivideo.hunyuan.tencent.com
https://api.aida.qq.com
https://api.thumbmarkjs.com
https://apps.apple.com
https://asr.cloud.tencent.com
https://beian.cac.gov.cn
https://beian.miit.gov.cn
https://beian.mps.gov.cn
https://bizmid-material-qaa-1258344703.file.myqcloud.com
https://careers.tencent.com
https://cdn-bot.hunyuan.tencent.com
https://cdn-hybrid-prod.hunyuan.tencent.com
https://cdn-hybrid-qa.hunyuan.tencent.com
https://cdn-portal.hunyuan.tencent.com
https://cloud.tencent.com
https://config.ab.qq.com
https://data.ab.qq.com
https://dit.hunyuan.tencent.com
https://doc.weixin.qq.com
https://docs.pmnd.rs
https://docs.qq.com
https://galileotelemetry.tencent.com
https://geo.datav.aliyun.com
https://git.io
https://github.com
https://github.githubassets.com
https://graph.qq.com
https://h.trace.qq.com
https://htrace.wetvinfo.com
https://hunyuan-apikey.woa.com
https://hunyuan-base-prod-1258344703.cos.ap-guangzhou.myqcloud.com
https://hunyuan-bot-qa-1258344703.cos.ap-guangzhou.myqcloud.com
https://hunyuan-hybrid-prod-1258344703.cos.ap-guangzhou.myqcloud.com
https://hunyuan-portal-1258344703.cos.ap-nanjing.myqcloud.com
https://hunyuan-portal-prod-1258344703.cos.ap-guangzhou.myqcloud.com
https://hunyuan-portal-qa-1258344703.cos.ap-guangzhou.myqcloud.com
https://hunyuan-prod-1258344703.cos.ap-guangzhou.myqcloud.com
https://hunyuan.tencent.com
https://hunyuan.woa.com
https://hunyuanpre-apikey.woa.com
https://hunyuantest-apikey.woa.com
https://hy-apikey-manager-1258344703.cos.ap-guangzhou.myqcloud.com
https://hy-model-ap-prod-1258344703.cos.ap-guangzhou.myqcloud.com
https://hy-openapi-public-1258344703.cos.ap-nanjing.myqcloud.com
https://hy.tencent.com
https://ima.qq.com
https://iwiki.woa.com
https://kf.qq.com
https://lmarena.ai
https://map.qq.com
https://mozilla.github.io
https://new.qq.com
https://npms.io
https://oth.str.beacon.qq.com
https://otheve.beacon.qq.com
https://privacy.qq.com
https://qcloudimg.tencent-cloud.cn
https://r.hrc.woa.com
https://raw.githack.com
https://react-dnd.github.io
https://reactjs.org
https://reactrouter.com
https://redux.js.org
https://rule.tencent.com
https://staticfile.qq.com
https://stuk.github.io
https://svibeacon.onezapp.com
https://tai.it.tencent.com
https://tconfig.ab.qq.com
https://tdata.ab.qq.com
https://tdesign.gtimg.com
https://test.hunyuan.woa.com
https://test2.hunyuan.woa.com
https://tun-cos-1258344701.file.myqcloud.com
https://unpkg.com
https://updatecdn.meeting.qq.com
https://url
https://video.test.hunyuan.woa.com
https://view.officeapps.live.com
https://vm.gtimg.cn
https://www.cac.gov.cn
https://www.gstatic.com
https://www.iquilezles.org
https://www.latexlive.com
https://www.shadertoy.com
https://www.tencent.com
https://www.webrtc-experiment.com
https://xj-psd-1258344703.cos.ap-guangzhou.myqcloud.com
https://xj-psd-1258344703.file.myqcloud.com
https://y.qq.com
https://yuanbao.tencent.com
https://yuanbao.test.hunyuan.woa.com
https://zhuanhuabao-5gqf84946751a1cb-1300912497.tcloudbaseapp.com
