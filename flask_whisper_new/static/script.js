// =============================
// 全域變數
// =============================
let recorder = null;
let mediaStream = null;
let isRecording = false;

let recognition = null;
let recognizing = false;

let fullOriginal = "";
let fullTranslated = "";

let sessionChunks = [];

let isPaused = false;
let elapsedSeconds = 0;
let timerInterval = null;


// =============================
// 初始化語音辨識
// =============================
function initSTT() {
    const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
    if (!SR) {
        alert("此瀏覽器不支援語音辨識，請使用 Chrome 或 Edge。");
        return;
    }

    // =============================
    // Speech Recognition：即時語音辨識
    // =============================
    recognition = new SR();
    recognition.continuous = true;
    recognition.interimResults = true;

    recognition.onresult = (event) => {
        let partial = "";
        for (let i = event.resultIndex; i < event.results.length; i++) {
            partial += event.results[i][0].transcript;
        }

        // 顯示來源文字（即時）
        document.getElementById("raw_output").innerText =
            fullOriginal + "\n" + partial;

        // 同步翻譯
        debounceTranslate(fullOriginal + "\n" + partial);
    };

    recognition.onerror = (e) => console.error("STT Error:", e);
}  // ❗ initSTT 正確結束


// =============================
// 前端語言偵測（非常重要）
// =============================
function detectClientLang(text) {
    let zhCount = (text.match(/[\u4e00-\u9fff]/g) || []).length;
    let enCount = (text.match(/[a-zA-Z]/g) || []).length;

    if (enCount > zhCount) return "en"; // 英文優先

    // 判斷繁簡：最準方法
    const traditionalRegex = /[\u3100-\u312F\u31A0-\u31BF\u2E80-\u2EFF\uF900-\uFAFF]/;
    return traditionalRegex.test(text) ? "zh-TW" : "zh-CN";
}


// =============================
// 翻譯防抖動 debounce
// =============================
let translateTimer = null;
function debounceTranslate(text) {
    if (translateTimer) clearTimeout(translateTimer);
    translateTimer = setTimeout(() => {
        sendForTranslate(text);
    }, 500);
}


// =============================
// 傳文字至後端翻譯 API
// =============================
function sendForTranslate(text) {
    let src = document.getElementById("src_lang").value;

    // 若 UI 設定為 auto → 自動偵測
    if (src === "auto") {
        src = detectClientLang(text);
        console.log("偵測來源語言:", src);
    }

    let tgt = document.getElementById("tgt_lang").value;

    fetch("/translate_api", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({
            text: text,
            src_lang: src,
            tgt_lang: tgt
        })
    })
        .then(res => res.json())
        .then(data => {
            fullTranslated = data.translated || "";
            document.getElementById("translated_output").innerText = fullTranslated;
        })
        .catch(err => console.error("Translation error:", err));
}


// =============================
// 開始錄音
// =============================
async function startRecording() {
    if (!recognition) initSTT();

    mediaStream = await navigator.mediaDevices.getUserMedia({audio: true});
    recorder = new MediaRecorder(mediaStream);
    sessionChunks = [];

    recorder.ondataavailable = (e) => {
        if (e.data && e.data.size > 0) {
            sessionChunks.push(e.data);
        }
    };

    recorder.start(1000);
    recognition.start();

    isRecording = true;
    isPaused = false;

    elapsedSeconds = 0;
    timerInterval = setInterval(() => {
        elapsedSeconds++;
        updateTimer();
    }, 1000);

    document.getElementById("btnStart").disabled = true;
    document.getElementById("btnPause").disabled = false;
    document.getElementById("btnStop").disabled = false;
}


// =============================
// 暫停 / 繼續
// =============================
function togglePause() {
    if (!isRecording) return;

    if (!isPaused) {
        recorder.pause();
        recognition.stop();
        isPaused = true;

        clearInterval(timerInterval);
        document.getElementById("btnPause").innerText = "繼續";

    } else {
        recorder.resume();
        recognition.start();
        isPaused = false;

        timerInterval = setInterval(() => {
            elapsedSeconds++;
            updateTimer();
        }, 1000);

        document.getElementById("btnPause").innerText = "暫停";
    }
}


// =============================
// 點停止 → 出現詢問視窗
// =============================
function stopRecording() {
    if (!isRecording) return;

    isPaused = true;
    recorder.pause();
    recognition.stop();

    clearInterval(timerInterval);

    document.getElementById("btnPause").disabled = true;
    document.getElementById("btnStop").disabled = true;

    openStopModal();
}


// =============================
// Modal 控制：停止 → 是否保存
// =============================
function openStopModal() {
    document.getElementById("stopModal").style.display = "flex";
}

function closeStopModal() {
    document.getElementById("stopModal").style.display = "none";

    recorder.resume();
    recognition.start();
    isPaused = false;

    timerInterval = setInterval(() => {
        elapsedSeconds++;
        updateTimer();
    }, 1000);

    document.getElementById("btnPause").disabled = false;
    document.getElementById("btnStop").disabled = false;
}


// =============================
// 放棄錄音
// =============================
function openDiscardConfirm() {
    document.getElementById("discardModal").style.display = "flex";
}

function closeDiscardModal() {
    document.getElementById("discardModal").style.display = "none";
}

function confirmDiscard() {
    closeDiscardModal();
    document.getElementById("stopModal").style.display = "none";

    recorder.stop();
    recognition.stop();
    isRecording = false;

    fullOriginal = "";
    fullTranslated = "";
    document.getElementById("raw_output").innerText = "";
    document.getElementById("translated_output").innerText = "";
    elapsedSeconds = 0;

    updateTimer();
    document.getElementById("btnStart").disabled = false;
}


// =============================
// 保存錄音與翻譯
// =============================
function openSaveNameModal() {
    document.getElementById("saveNameModal").style.display = "flex";
}

function closeSaveNameModal() {
    document.getElementById("saveNameModal").style.display = "none";
}

function confirmSave() {
    let name = document.getElementById("saveNameInput").value.trim();

    if (!sessionChunks.length) {
        alert("沒有錄音資料可保存。");
        return;
    }

    let audioBlob = new Blob(sessionChunks, {
        type: recorder.mimeType || "audio/webm"
    });

    if (audioBlob.size === 0) {
        alert("音檔為空");
        return;
    }

    let form = new FormData();
    form.append("name", name);
    form.append("original_text", fullOriginal);
    form.append("translated_text", fullTranslated);
    form.append("audio", audioBlob, "session.webm");

    fetch("/save_session", {
        method: "POST",
        body: form
    })
        .then(res => res.json())
        .then(data => {
            if (data.success) {
                alert(`已保存：${data.name}`);
                closeSaveNameModal();
                document.getElementById("stopModal").style.display = "none";

                loadTasks();
            } else {
                alert("保存失敗：" + data.error);
            }
        });
}


// =============================
// 任務列表
// =============================
function loadTasks() {
    fetch("/tasks")
        .then(res => res.json())
        .then(data => {
            const ul = document.getElementById("taskList");
            ul.innerHTML = "";
            data.forEach(task => {
                const li = document.createElement("li");
                li.className = "list-group-item list-group-item-action";
                li.innerText = task.name;
                li.onclick = () => openTask(task.id);
                ul.appendChild(li);
            });
        });
}


// =============================
// 顯示任務詳細
// =============================
function openTask(id) {
    fetch(`/task/${id}`)
        .then(res => res.json())
        .then(task => {
            document.getElementById("taskDetailCard").classList.remove("d-none");
            document.getElementById("taskDetailTitle").innerText = task.name;
            document.getElementById("taskDetailOriginal").innerText = task.original;
            document.getElementById("taskDetailTranslated").innerText = task.translated;
            document.getElementById("taskAudio").src = task.audio_url;
        });
}


// =============================
// 更新錄音計時器
// =============================
function updateTimer() {
    let m = String(Math.floor(elapsedSeconds / 60)).padStart(2, "0");
    let s = String(elapsedSeconds % 60).padStart(2, "0");
    document.getElementById("timerDisplay").innerText = `${m}:${s}`;
}


// =============================
window.onload = () => {
    loadTasks();
};
