import AppKit
import Foundation
import Vision

var labelScores: [String: Float] = [:]
var recognizedTexts: [String] = []

for path in CommandLine.arguments.dropFirst() {
    guard let image = NSImage(contentsOfFile: path) else { continue }
    var rect = NSRect(origin: .zero, size: image.size)
    guard let cgImage = image.cgImage(
        forProposedRect: &rect,
        context: nil,
        hints: nil
    ) else { continue }

    let classify = VNClassifyImageRequest()
    let recognize = VNRecognizeTextRequest()
    recognize.recognitionLevel = .accurate
    recognize.usesLanguageCorrection = true

    let handler = VNImageRequestHandler(cgImage: cgImage)
    try handler.perform([classify, recognize])

    for result in (classify.results ?? []).prefix(12) {
        if result.confidence < 0.15 { continue }
        labelScores[result.identifier] = max(
            labelScores[result.identifier] ?? 0,
            result.confidence
        )
    }

    for result in recognize.results ?? [] {
        guard let text = result.topCandidates(1).first?.string else { continue }
        if !recognizedTexts.contains(text) {
            recognizedTexts.append(text)
        }
    }
}

let labels: [[String: Any]] = labelScores
    .sorted { $0.value > $1.value }
    .prefix(12)
    .map { ["label": $0.key, "confidence": Double($0.value)] }
let output: [String: Any] = [
    "labels": labels,
    "texts": recognizedTexts,
]
let data = try JSONSerialization.data(withJSONObject: output)
print(String(data: data, encoding: .utf8)!)
