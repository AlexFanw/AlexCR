import matplotlib.pyplot as plt
import matplotlib.colors as mcolors

x = [1, 2, 3, 4, 5]
y1 = [0.519,0.573,0.584,0.605,0.613]
y2 = [0.544,0.643,0.667,0.681,0.692]
y3 = [0.572,0.653,0.689,0.701,0.713]
y4 = [0.525,0.667,0.775,0.811,0.834]
plt.title('Amazon-Book', fontsize="16")  # 折线图标题
plt.xlabel('Number of asked attribute instances', fontsize="16")  # x轴标题
plt.ylabel('Success Rate@15', fontsize="16")  # y轴标题
plt.plot(x, y1, marker= "o", markersize=10, color=mcolors.to_rgb('#558cba'))  # 绘制折线图，添加数据点，设置点的大小
plt.plot(x, y2, marker= "P", markersize=10, color=mcolors.to_rgb('#15254b'))
plt.plot(x, y3, marker= "D", markersize=8, color=mcolors.to_rgb('#ea832f'))
plt.plot(x, y4, marker= "*", markersize=13, color=mcolors.to_rgb('#e9ba50'))

plt.legend(['UNICORN-M','MCMIPL-M','DAHCR-M','CoCHPL'])  # 设置折线名称
plt.xticks(x)
plt.xticks(fontsize=14)
plt.yticks(fontsize=14)
plt.savefig('./paper/book.png', dpi=300)
# plt.show()  # 显示折线图