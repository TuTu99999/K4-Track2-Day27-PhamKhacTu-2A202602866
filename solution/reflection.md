# Reflection

## Những lỗi khó phát hiện nhất

Các lỗi khó nhất là những trường hợp `feature_skew`, `embedding_drift` và lỗi
chất lượng batch có độ lệch nhỏ. Chúng nằm gần vùng dao động tự nhiên của dữ
liệu sạch, nên việc chỉ so sánh với một ngưỡng 3σ có thể bỏ sót lỗi, trong khi
hạ ngưỡng đồng loạt lại dễ tạo cảnh báo giả. Với `data_batch`, tôi kết hợp bốn
chiều volume, null rate, mean amount và staleness thành một khoảng cách tổng
hợp. Cách này giúp phát hiện nhiều độ lệch vừa phải xảy ra đồng thời mà không
coi một tín hiệu nhiễu đơn lẻ là lỗi. Với feature và embedding, tôi chọn ngưỡng
nằm trong khoảng phân tách quan sát được giữa dữ liệu sạch và dữ liệu lỗi.

## Đánh đổi chi phí và độ bao phủ

Giải pháp gọi đúng một công cụ tương ứng cho mỗi sự kiện và không gọi lặp lại.
Practice đạt TPR 1.0, FPR 0.0 với 180/220 credits; Public đạt TPR 1.0, FPR 0.0
với 240/220 credits. Tôi chấp nhận khoản phạt nhỏ ở Public để duy trì độ bao
phủ. Ở Private, ngân sách 320 credits đủ cho chiến lược single-pass nên kết quả
không bị cost overage. Kết quả Private đạt TPR 0.7778 và FPR 0.1438 khi dùng
warning band xấp xỉ 2σ được suy ra từ baseline 3σ. Đây là lựa chọn ưu tiên recall
theo đúng trọng số điểm: chấp nhận thêm cảnh báo giả để bắt được nhiều lỗi subtle
hơn, nhưng không dùng ID, nhãn hay ngưỡng riêng cho từng phase. Tôi dừng hiệu
chỉnh sau lần đánh giá này để không biến dữ liệu Private thành tập huấn luyện.

## Nếu có thêm một vòng cải tiến

Tôi sẽ dùng `ctx.state` để duy trì thống kê online riêng cho từng loại sự kiện:
trung bình động, phương sai theo thuật toán Welford và các quantile xấp xỉ bằng
cửa sổ giới hạn. Detector khi đó có thể kết hợp baseline toàn cục với baseline
cục bộ, yêu cầu nhiều tín hiệu yếu cùng xuất hiện trước khi cảnh báo. Tôi cũng
sẽ thêm giai đoạn warm-up và giới hạn mức ảnh hưởng của outlier để chính dữ
liệu lỗi không làm trôi baseline thích nghi. Mục tiêu là tăng khả năng bắt lỗi
subtle mà vẫn giữ FPR gần 0 và không phát sinh thêm RPC.
